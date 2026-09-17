/**
 * recorder.ts — ProseMirror steps → op bodies (09-editor §4).
 *
 * Given the document a transaction started from, its steps, and the shadows
 * of the blocks it touches, produce the ops the calculus needs and advance the
 * shadows. Exact for every step that stays inside one textblock: a
 * `ReplaceStep` is a delete of the identities under its range and an insert
 * after the identity before it; `AddMarkStep`/`RemoveMarkStep` are `text.mark`.
 *
 * Anything else — a step whose range or slice crosses a block boundary, or a
 * step kind this module does not name — is returned as `unhandled`, and the
 * caller falls back to per-block reconciliation (`reconcile`, below), which is
 * confined, named, and never decides who wrote anything: the actor is the
 * person either way. Structural gestures never reach this module: the keymap's
 * commands emit their own ops (commands.ts) and tag the transaction so the
 * recorder skips it.
 *
 * Positions: ProseMirror counts UTF-16 units inside text nodes; the calculus
 * counts Unicode scalars. Conversion happens here and only here.
 */
import type { Node as PMNode, Slice } from '@tiptap/pm/model';
import { AddMarkStep, RemoveMarkStep, ReplaceStep, type Step } from '@tiptap/pm/transform';

import { START, scalars, type Anchor, type CharRef, type OpBody } from './projection';
import { Shadow } from './shadow';

export interface Recorded {
    ops: OpBody[];
    /** Client ids for the inserts, in op order, so the caller can pair acks. */
    cids: string[];
    unhandled: Step[];
}

export interface BlockAt {
    bid: string;
    path: string;
    /** Position of the block node in the doc the step applies to. */
    pos: number;
    node: PMNode;
}

/** The mark kinds the recorder sends; everything else is painted. */
const RECORDED = new Set(['bold', 'link', 'cite']);

export class Recorder {
    private n = 0;
    constructor(private readonly shadows: Map<string, Shadow>, private readonly nextCid: () => string = () => `ide-${Date.now().toString(36)}-${(this.n++).toString(36)}`) {}

    /** Record `steps`, each against the doc it applies to (`docs[i]`). */
    record(steps: readonly Step[], docs: readonly PMNode[]): Recorded {
        const out: Recorded = { ops: [], cids: [], unhandled: [] };
        steps.forEach((step, i) => {
            const doc = docs[i];
            if (step instanceof ReplaceStep) {
                if (!this.replace(step, doc, out)) out.unhandled.push(step);
            } else if (step instanceof AddMarkStep || step instanceof RemoveMarkStep) {
                if (!this.mark(step, doc, out)) out.unhandled.push(step);
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
        const target = { bid, path: 'text' };
        if (toOff > fromOff) {
            const from = shadow.ref(fromOff);
            const to = shadow.ref(toOff - 1);
            out.ops.push({ kind: 'text.delete', target, payload: { from, to } });
            shadow.delete(fromOff, toOff);
        }
        const text = sliceText(step.slice);
        if (text) {
            const cid = this.nextCid();
            const after: Anchor = shadow.anchorBefore(fromOff);
            out.ops.push({ kind: 'text.insert', target, payload: { after, text } });
            out.cids.push(cid);
            shadow.insert(fromOff, text, cid);
            // Marks carried by the inserted text, as ranges over the minted chars.
            let k = 0;
            step.slice.content.forEach(child => {
                if (child.isText) {
                    const len = scalars(child.text ?? '').length;
                    for (const m of child.marks) {
                        if (!RECORDED.has(m.type.name) || len === 0) continue;
                        out.ops.push({ kind: 'text.mark', target, payload: {
                            kind: m.type.name, attrs: markAttrs(m.type.name, m.attrs),
                            start: ['$cid', cid, k] as CharRef, end: ['$cid', cid, k + len - 1] as CharRef, on: true,
                        } });
                    }
                    k += len;
                } else if (child.type.name === 'hardBreak') {
                    k += 1;
                }
            });
        }
        return true;
    }

    private mark(step: AddMarkStep | RemoveMarkStep, doc: PMNode, out: Recorded): boolean {
        if (!RECORDED.has(step.mark.type.name)) return true;   // a painted mark: nothing to send
        const $from = doc.resolve(step.from);
        const $to = doc.resolve(step.to);
        if ($from.depth !== 1 || $to.depth !== 1 || $from.parent !== $to.parent) return false;
        const block = $from.parent;
        const bid = block.attrs.bid as string | null;
        const shadow = bid ? this.shadows.get(bid) : undefined;
        if (!bid || !shadow) return false;
        const fromOff = scalarOffset(block, $from.parentOffset);
        const toOff = scalarOffset(block, $to.parentOffset);
        if (toOff <= fromOff) return true;
        out.ops.push({ kind: 'text.mark', target: { bid, path: 'text' }, payload: {
            kind: step.mark.type.name, attrs: markAttrs(step.mark.type.name, step.mark.attrs),
            start: shadow.ref(fromOff), end: shadow.ref(toOff - 1), on: step instanceof AddMarkStep,
        } });
        return true;
    }
}

// ── helpers ──────────────────────────────────────────────────────────────────

/** Scalar offset within a textblock for a UTF-16 parentOffset (hard breaks count 1). */
export function scalarOffset(block: PMNode, parentOffset: number): number {
    let units = 0;
    let out = 0;
    for (let i = 0; i < block.childCount && units < parentOffset; i++) {
        const child = block.child(i);
        if (child.isText) {
            const t = child.text ?? '';
            const take = Math.min(t.length, parentOffset - units);
            out += scalars(t.slice(0, take)).length;
            units += take;
        } else {
            out += 1;
            units += child.nodeSize;
        }
    }
    return out;
}

/** The plain text of an inline slice; a hard break is one newline scalar. */
export function sliceText(slice: Slice): string {
    let s = '';
    slice.content.forEach(child => {
        if (child.isText) s += child.text ?? '';
        else if (child.type.name === 'hardBreak') s += '\n';
    });
    return s;
}

function inlineOnly(slice: Slice): boolean {
    let ok = true;
    slice.content.forEach(child => { if (!child.isInline) ok = false; });
    return ok;
}

function markAttrs(kind: string, attrs: Record<string, unknown>): Record<string, string> {
    if (kind === 'link') return { href: String(attrs.href ?? '') };
    if (kind === 'cite') return { file: String(attrs.file ?? ''), symbol: String(attrs.symbol ?? '') };
    return {};
}

/**
 * The confined fallback (09-editor §4): a block whose inline content changed
 * through a step the recorder could not name. Compares the block's current
 * scalars with its shadow as one replaced span — the shortest common prefix and
 * suffix survive, everything between is deleted and re-inserted. The actor is
 * the person either way; only run boundaries can be coarser than recording.
 */
export function reconcile(block: PMNode, shadow: Shadow, nextCid: () => string): { ops: OpBody[]; cids: string[] } {
    const bid = block.attrs.bid as string;
    const now = scalars(block.textBetween(0, block.content.size, '\n'));
    const was = shadow.chars;
    let p = 0;
    while (p < now.length && p < was.length && now[p] === was[p]) p++;
    let s = 0;
    while (s < now.length - p && s < was.length - p && now[now.length - 1 - s] === was[was.length - 1 - s]) s++;
    const ops: OpBody[] = [];
    const cids: string[] = [];
    const target = { bid, path: 'text' };
    const delTo = was.length - s;
    if (delTo > p) {
        ops.push({ kind: 'text.delete', target, payload: { from: shadow.ref(p), to: shadow.ref(delTo - 1) } });
        shadow.delete(p, delTo);
    }
    const inserted = now.slice(p, now.length - s).join('');
    if (inserted) {
        const cid = nextCid();
        ops.push({ kind: 'text.insert', target, payload: { after: shadow.anchorBefore(p), text: inserted } });
        cids.push(cid);
        shadow.insert(p, inserted, cid);
    }
    return { ops, cids };
}

export { START };
