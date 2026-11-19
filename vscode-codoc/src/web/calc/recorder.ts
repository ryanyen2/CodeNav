/**
 * recorder.ts: ProseMirror steps → op bodies.
 *
 * Given the document a transaction started from, its steps, and the shadows
 * of the blocks it touches, produce the ops the calculus needs and advance the
 * shadows. Exact for every step that stays inside one textblock: a
 * `ReplaceStep` is a delete of the identities under its range (as segments,
 * never a range, so a word another actor inserted there and the page has not
 * seen survives) and an insert after the identity before it.
 * `AddMarkStep`/`RemoveMarkStep` are one `text.mark` per textblock the step
 * covers.
 *
 * Anything else (a replace whose range or slice crosses a block boundary, or a
 * step kind this module does not name) is returned as `unhandled`, and the
 * caller falls back to per-block reconciliation (`reconcile`, below), which
 * never decides who wrote anything: the actor is the person either way.
 * Structural gestures never reach this module: the keymap's commands emit
 * their own ops (commands.ts) and tag the transaction so the recorder skips it.
 *
 * The op's `path` is the block's Text field: `source` for a table, a diagram
 * or a code block, `text` otherwise.
 *
 * Positions: ProseMirror counts UTF-16 units inside text nodes; the calculus
 * counts Unicode scalars. `scalarOffset` converts one way here, and
 * `unitOffset` in editor/decorations.ts converts back.
 */
import type { Fragment, Node as PMNode, Slice } from '@tiptap/pm/model';
import { AddMarkStep, RemoveMarkStep, ReplaceStep, type Step } from '@tiptap/pm/transform';

import { kindOf, nfc, pathOf, scalars, type Anchor, type CharRef, type OpBody } from './projection';
import { Shadow, segmentsOf } from './shadow';

export interface Recorded {
    ops: OpBody[];
    /** Client ids for the inserts, in op order, so the caller can pair acks. */
    cids: string[];
    unhandled: Step[];
}

/** The mark kinds a person sets and the recorder sends. The other, `comment`,
 *  comes from the projection and is never sent. */
export const SENT_MARKS: ReadonlySet<string> = new Set(['bold', 'code', 'link', 'cite']);

/** The op target for a block node: its id and its Text field. */
export const targetOf = (block: PMNode): { bid: string; path: string } =>
    ({ bid: block.attrs.bid as string, path: pathOf(kindOf(block)) });

/** A `text.delete` of exactly these characters, by identity. */
export const deleteOf = (target: { bid: string; path: string }, refs: readonly CharRef[]): OpBody =>
    ({ kind: 'text.delete', target, payload: { segments: segmentsOf(refs) } });

export class Recorder {
    constructor(private readonly shadows: Map<string, Shadow>, private readonly nextCid: () => string) {}

    /** Record `steps`, each against the doc it applies to (`docs[i]`). */
    record(steps: readonly Step[], docs: readonly PMNode[]): Recorded {
        const out: Recorded = { ops: [], cids: [], unhandled: [] };
        steps.forEach((step, i) => {
            const doc = docs[i];
            if (step instanceof ReplaceStep) {
                if (!this.replace(step, doc, out)) out.unhandled.push(step);
            } else if (step instanceof AddMarkStep || step instanceof RemoveMarkStep) {
                this.mark(step, doc, out);
            } else {
                out.unhandled.push(step);
            }
        });
        return out;
    }

    // ── steps ──────────────────────────────────────────────────────────────

    private replace(step: ReplaceStep, doc: PMNode, out: Recorded): boolean {
        const $from = doc.resolve(step.from);
        const $to = doc.resolve(step.to);
        if ($from.depth !== 1 || $to.depth !== 1 || $from.parent !== $to.parent) return false;
        if (step.slice.openStart !== 0 || step.slice.openEnd !== 0) return false;
        if (!inlineOnly(step.slice)) return false;
        const block = $from.parent;
        const bid = block.attrs.bid as string | null;
        const shadow = bid ? this.shadows.get(bid) : undefined;
        if (!bid || !shadow) return false;
        const fromOff = scalarOffset(block, $from.parentOffset);
        const toOff = scalarOffset(block, $to.parentOffset);
        const target = targetOf(block);
        if (toOff > fromOff) out.ops.push(deleteOf(target, shadow.delete(fromOff, toOff)));
        const text = sliceText(step.slice);
        if (text) {
            const cid = this.nextCid();
            const after: Anchor = shadow.anchorBefore(fromOff);
            out.ops.push({ kind: 'text.insert', target, payload: { after, text } });
            out.cids.push(cid);
            shadow.insert(fromOff, text, cid);
            out.ops.push(...markOps(target, cid, step.slice.content));
        }
        return true;
    }

    /** One `text.mark` per textblock the step covers. */
    private mark(step: AddMarkStep | RemoveMarkStep, doc: PMNode, out: Recorded): void {
        if (!SENT_MARKS.has(step.mark.type.name)) return;   // drawn from the projection: nothing to send
        doc.nodesBetween(step.from, step.to, (block, pos, parent) => {
            if (parent !== doc) return true;
            const shadow = this.shadows.get(block.attrs.bid as string);
            if (!shadow || !block.type.allowsMarkType(step.mark.type)) return false;
            const start = pos + 1;
            const fromOff = scalarOffset(block, Math.max(step.from, start) - start);
            const toOff = scalarOffset(block, Math.min(step.to, start + block.content.size) - start);
            if (toOff <= fromOff) return false;
            out.ops.push({ kind: 'text.mark', target: targetOf(block), payload: {
                kind: step.mark.type.name, attrs: markAttrs(step.mark.type.name, step.mark.attrs),
                start: shadow.ref(fromOff), end: shadow.ref(toOff - 1), on: step instanceof AddMarkStep,
            } });
            return false;
        });
    }
}

// ── helpers ──────────────────────────────────────────────────────────────────

/** Scalar offset within a textblock for a UTF-16 parentOffset. */
export function scalarOffset(block: PMNode, parentOffset: number): number {
    let units = 0;
    let out = 0;
    for (let i = 0; i < block.childCount && units < parentOffset; i++) {
        const child = block.child(i);
        const t = child.text ?? '';
        const take = Math.min(t.length, parentOffset - units);
        out += scalars(t.slice(0, take)).length;
        units += take;
    }
    return out;
}

/** A `text.mark` for each sent mark on the text nodes of `content`, over the
 *  characters the insert `cid` minted for them. */
function markOps(target: { bid: string; path: string }, cid: string, content: Fragment): OpBody[] {
    const out: OpBody[] = [];
    let k = 0;
    content.forEach(child => {
        const len = scalars(nfc(child.text ?? '')).length;
        for (const m of child.marks) {
            if (!SENT_MARKS.has(m.type.name) || len === 0) continue;
            out.push({ kind: 'text.mark', target, payload: {
                kind: m.type.name, attrs: markAttrs(m.type.name, m.attrs),
                start: ['$cid', cid, k] as CharRef, end: ['$cid', cid, k + len - 1] as CharRef, on: true,
            } });
        }
        k += len;
    });
    return out;
}

/** The plain text of an inline slice, NFC as the applier records it. */
function sliceText(slice: Slice): string {
    let s = '';
    slice.content.forEach(child => { s += child.text ?? ''; });
    return nfc(s);
}

function inlineOnly(slice: Slice): boolean {
    let ok = true;
    slice.content.forEach(child => { if (!child.isText) ok = false; });
    return ok;
}

export function markAttrs(kind: string, attrs: Record<string, unknown>): Record<string, string> {
    if (kind === 'link') return { href: String(attrs.href ?? '') };
    if (kind === 'cite') return { symbol: String(attrs.symbol ?? '') };
    return {};
}

/**
 * The fallback, and the record of an IME composition: a block whose text
 * changed through steps the recorder did not read. Compares the block's current
 * scalars with its shadow as one replaced span: the longest common prefix and
 * suffix survive, everything between is deleted and re-inserted. The inserted
 * characters keep the marks the page shows on them, as typed text does. The
 * actor is the person either way; only run boundaries can be coarser than
 * recording.
 */
export function reconcile(block: PMNode, shadow: Shadow, nextCid: () => string): { ops: OpBody[]; cids: string[] } {
    const target = targetOf(block);
    const text = block.textContent;
    const r = reconcileText(target, text, shadow, nextCid);
    if (r.cids.length) {
        const from = scalars(text).slice(0, r.at).join('').length;
        const inserted = r.ops[r.ops.length - 1].payload.text as string;
        r.ops.push(...markOps(target, r.cids[0], block.content.cut(from, from + inserted.length)));
    }
    return r;
}

/** The same comparison for a field's plain text, as a card's text area holds
 *  it. `at` is the offset of the insert, when there is one. */
export function reconcileText(target: { bid: string; path: string }, text: string, shadow: Shadow,
                              nextCid: () => string): { ops: OpBody[]; cids: string[]; at: number } {
    const now = scalars(nfc(text));
    const was = shadow.chars;
    let p = 0;
    while (p < now.length && p < was.length && now[p] === was[p]) p++;
    let s = 0;
    while (s < now.length - p && s < was.length - p && now[now.length - 1 - s] === was[was.length - 1 - s]) s++;
    const ops: OpBody[] = [];
    const cids: string[] = [];
    const delTo = was.length - s;
    if (delTo > p) ops.push(deleteOf(target, shadow.delete(p, delTo)));
    const inserted = now.slice(p, now.length - s).join('');
    if (inserted) {
        const cid = nextCid();
        ops.push({ kind: 'text.insert', target, payload: { after: shadow.anchorBefore(p), text: inserted } });
        cids.push(cid);
        shadow.insert(p, inserted, cid);
    }
    return { ops, cids, at: p };
}
