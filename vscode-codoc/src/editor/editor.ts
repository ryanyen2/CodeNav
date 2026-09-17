/**
 * editor.ts — the ProseMirror view over a projection (09-editor §3–7).
 *
 * `mountEditor` builds the document from a projection, keeps a shadow per
 * block, records every user transaction into ops (calc/recorder.ts) or
 * performs a structural gesture through a command (commands.ts), submits ops
 * in debounced batches, resolves acks, and adopts new projections per block
 * while leaving the blocks the person is holding alone.
 *
 * ProseMirror's own history is not installed: undo is the recorded inverse
 * applied as a new op, so two histories of one document cannot diverge.
 */
import { type Node as PMNode, type Schema } from '@tiptap/pm/model';
import { EditorState, Plugin, PluginKey, TextSelection, type Transaction } from '@tiptap/pm/state';
import { EditorView } from '@tiptap/pm/view';
import { keymap } from '@tiptap/pm/keymap';
import { baseKeymap } from '@tiptap/pm/commands';
import { InputRule, inputRules } from '@tiptap/pm/inputrules';

import { adoptionPlan, blockNode, docFrom } from '../calc/adopt';
import { paintsFor, type Paint } from '../calc/claims';
import { NO_WATERMARKS, type Ack, type OpBody, type Projection, type Submission } from '../calc/projection';
import { Recorder, reconcile } from '../calc/recorder';
import type { Shadow } from '../calc/shadow';
import { RECORDED, deleteFeature, makeHeading, mergeBackward, mergeForward, moveBlock, relevel, splitBlock, type Env } from './commands';
import { buildDecorations } from './decorations';
import { codocSchema } from './schema';

export interface EditorHost {
    /** Append submissions to the inbox (the host process writes the file). */
    submit(subs: Submission[]): void;
    /** A heading was clicked / the caret entered a feature. */
    onFeature?(fid: string | null): void;
    /** Open a code location (a cite mark). */
    openCite?(file: string, symbol: string): void;
}

export interface EditorHandle {
    view: EditorView;
    adopt(proj: Projection, realizing?: ReadonlySet<string>): void;
    ack(acks: Ack[]): void;
    flush(): void;
    featureAt(pos: number): string | null;
    deleteFeature(fid: string): void;
    moveBlock(bid: string, afterBid: string | 'START', levelDelta?: number): void;
    projection(): Projection | null;
    destroy(): void;
}

const decoKey = new PluginKey('codoc-paints');
const SETTLE_MS = 350;

let seq = 0;
const newBid = () => `b-${Math.random().toString(16).slice(2, 14).padEnd(12, '0')}`;
const newCid = () => `ide-${Date.now().toString(36)}-${(seq++).toString(36)}`;
const newBatch = () => `t-${Math.random().toString(16).slice(2, 14)}`;

export function mountEditor(container: HTMLElement, proj: Projection, host: EditorHost): EditorHandle {
    const schema = codocSchema();
    const { doc, shadows } = docFrom(schema, proj);
    let projection: Projection = proj;
    let realizing: ReadonlySet<string> = new Set();
    /** Client ids awaiting an ack, by block. */
    const unacked = new Map<string, Set<string>>();
    /** Submissions not yet flushed. */
    let pending: Submission[] = [];
    let batch = newBatch();
    let settleTimer: number | undefined;
    const env: Env = { shadows, nextBid: newBid, nextCid: newCid };
    const recorder = new Recorder(shadows, newCid);

    const paints = new PluginKey<DecorationsState>('codoc-decos');
    interface DecorationsState { set: ReturnType<typeof buildDecorations> }

    const decoPlugin = new Plugin<DecorationsState>({
        key: paints,
        state: {
            init: (_c, state) => ({ set: buildDecorations(state.doc, paintMap(state.doc), projection.phases, realizing) }),
            apply: (tr, prev, _old, state) => tr.docChanged || tr.getMeta(decoKey)
                ? { set: buildDecorations(state.doc, paintMap(state.doc), projection.phases, realizing) }
                : prev,
        },
        props: { decorations: state => paints.getState(state)?.set ?? null },
    });

    function paintMap(d: PMNode): Map<string, Paint[]> {
        const out = new Map<string, Paint[]>();
        const byId = new Map(projection.blocks.map(b => [b.id, b]));
        let fid: string | null = null;
        d.forEach(block => {
            const bid = block.attrs.bid as string | null;
            if (!bid) return;
            if (block.type.name === 'heading') fid = bid;
            const pb = byId.get(bid);
            if (!pb) return;
            const marks = watermarksFor(fid);
            out.set(bid, paintsFor(pb, projection, fid, unackedOps(bid), marks));
        });
        return out;
    }

    function watermarksFor(fid: string | null): { handoff: Record<string, number>; realized: Record<string, number> } {
        return (fid && projection.watermarks?.[fid]) || NO_WATERMARKS;
    }

    function unackedOps(bid: string): Set<string> {
        return unacked.get(bid) ?? new Set();
    }

    // ── recording ──────────────────────────────────────────────────────────
    function record(tr: Transaction): void {
        const tagged = tr.getMeta(RECORDED) as OpBody[] | undefined;
        if (tagged) { queue(tagged, []); return; }
        if (!tr.docChanged) return;
        const rec = recorder.record(tr.steps, tr.docs);
        if (rec.unhandled.length) {
            // The confined fallback: reconcile every block the unhandled steps touched.
            const touched = new Set<string>();
            for (const step of rec.unhandled) {
                step.getMap().forEach((_a, _b, fromB, toB) => {
                    tr.doc.nodesBetween(Math.max(0, fromB - 1), Math.min(tr.doc.content.size, toB + 1), (n, _pos, parent) => {
                        if (parent === tr.doc && n.attrs.bid && shadows.has(n.attrs.bid)) touched.add(n.attrs.bid);
                        return false;
                    });
                });
            }
            for (const bid of touched) {
                let node: PMNode | null = null;
                tr.doc.forEach(n => { if (n.attrs.bid === bid) node = n; });
                if (node) {
                    const r = reconcile(node, shadows.get(bid)!, newCid);
                    rec.ops.push(...r.ops);
                    rec.cids.push(...r.cids);
                }
            }
        }
        queue(rec.ops, rec.cids);
    }

    function queue(ops: OpBody[], cids: string[]): void {
        let ci = 0;
        for (const op of ops) {
            const cid = op.kind === 'text.insert' ? (cids[ci++] ?? newCid()) : newCid();
            const bid = (op.target.bid ?? op.target.into ?? op.target.fid) as string | undefined;
            if (bid) unacked.set(bid, (unacked.get(bid) ?? new Set()).add(cid));
            pending.push({ cid, batch, ...op });
        }
        if (settleTimer) window.clearTimeout(settleTimer);
        settleTimer = window.setTimeout(flush, SETTLE_MS);
    }

    function flush(): void {
        if (settleTimer) { window.clearTimeout(settleTimer); settleTimer = undefined; }
        if (!pending.length) return;
        host.submit(pending);
        pending = [];
        batch = newBatch();
    }

    function ack(acks: Ack[]): void {
        for (const a of acks) {
            for (const [bid, set] of unacked) {
                if (set.delete(a.cid) && set.size === 0) unacked.delete(bid);
            }
            if (a.op) for (const sh of shadows.values()) sh.resolve(a.cid, a.op);
        }
        view.dispatch(view.state.tr.setMeta(decoKey, true));
    }

    // ── gestures ───────────────────────────────────────────────────────────
    const run = (g: ReturnType<typeof splitBlock>) => { if (!g) return false; view.dispatch(g.tr); return true; };
    const keys = keymap({
        Enter: state => run(splitBlock(state, env)),
        Backspace: state => { const g = mergeBackward(state, env); if (g) return run(g); return refuseHeadingMerge(state, 'back'); },
        Delete: state => { const g = mergeForward(state, env); if (g) return run(g); return refuseHeadingMerge(state, 'forward'); },
        Tab: state => run(relevel(state, env, 1)),
        'Shift-Tab': state => run(relevel(state, env, -1)),
        'Mod-z': () => true,     // undo is a recorded inverse, wired by the app
        'Mod-Shift-z': () => true,
        'Mod-y': () => true,
    });

    /** A deletion at a heading boundary moves the caret instead of merging. */
    function refuseHeadingMerge(state: EditorState, dir: 'back' | 'forward'): boolean {
        const { $from, empty } = state.selection;
        if (!empty || $from.depth !== 1) return false;
        if (dir === 'back' && $from.parentOffset === 0 && $from.index(0) > 0) {
            const prev = state.doc.child($from.index(0) - 1);
            if (prev.type.name === 'heading' || $from.parent.type.name === 'heading') {
                view.dispatch(state.tr.setSelection(TextSelection.create(state.doc, $from.before(1) - 1)));
                return true;
            }
        }
        if (dir === 'forward' && $from.parentOffset === $from.parent.content.size && $from.index(0) + 1 < state.doc.childCount) {
            const next = state.doc.child($from.index(0) + 1);
            if (next.type.name === 'heading' || $from.parent.type.name === 'heading') {
                view.dispatch(state.tr.setSelection(TextSelection.create(state.doc, $from.after(1) + 1)));
                return true;
            }
        }
        return false;
    }

    const headingRule = new InputRule(/^(#{1,6})\s$/, (state, match) => {
        const g = makeHeading(state, env, match[1].length, 0);
        if (!g) return null;
        // The typed `#…␠` were just recorded as inserts; drop them by identity.
        const b = state.doc.resolve(state.selection.from);
        const bid = b.node(1).attrs.bid as string;
        const sh = shadows.get(bid)!;
        const n = match[0].length;
        const refs = sh.delete(0, n);
        g.ops.unshift({ kind: 'text.delete', target: { bid, path: 'text' }, payload: { from: refs[0], to: refs[refs.length - 1] } });
        return g.tr.delete(b.before(1) + 1, b.before(1) + 1 + n).setMeta(RECORDED, g.ops);
    });

    const state = EditorState.create({
        schema,
        doc,
        plugins: [keys, inputRules({ rules: [headingRule] }), keymap(baseKeymap), decoPlugin],
    });

    const view = new EditorView(container, {
        state,
        dispatchTransaction(tr) {
            record(tr);
            const next = view.state.apply(tr);
            view.updateState(next);
            if (tr.selectionSet || tr.docChanged) host.onFeature?.(featureAt(next.selection.from));
        },
        handleClickOn(_v, _pos, node, _np, event) {
            const cite = (event.target as HTMLElement | null)?.closest?.('[data-cite]') as HTMLElement | null;
            if (cite && host.openCite) {
                const m = node.marks.find(x => x.type.name === 'cite') ?? null;
                if (m) { host.openCite(m.attrs.file, m.attrs.symbol); return true; }
            }
            return false;
        },
    });

    function featureAt(pos: number): string | null {
        const $p = view.state.doc.resolve(Math.min(pos, view.state.doc.content.size));
        if ($p.depth < 1) return null;
        for (let i = $p.index(0); i >= 0; i--) {
            const n = view.state.doc.child(i);
            if (n.type.name === 'heading') return n.attrs.bid as string;
        }
        return null;
    }

    // ── adoption ───────────────────────────────────────────────────────────
    function adopt(next: Projection, nowRealizing: ReadonlySet<string> = new Set()): void {
        flush();
        projection = next;
        realizing = nowRealizing;
        const current: Array<{ bid: string; head: number }> = [];
        view.state.doc.forEach(n => current.push({ bid: n.attrs.bid as string, head: n.attrs.head as number }));
        const held = new Set([...unacked.keys()].filter(b => (unacked.get(b)?.size ?? 0) > 0));
        const plan = adoptionPlan(current, next, held);
        if (!plan.length) { view.dispatch(view.state.tr.setMeta(decoKey, true)); return; }
        const byId = new Map(next.blocks.map(b => [b.id, b]));
        const sel = view.state.selection;
        const caretBid = featureBlockAt(sel.from);
        const caretOff = caretBid ? scalarCaret(sel.from) : 0;
        // Rebuild the document in the projection's order, keeping held blocks' nodes.
        const currentNodes = new Map<string, PMNode>();
        view.state.doc.forEach(n => currentNodes.set(n.attrs.bid as string, n));
        const nodes: PMNode[] = [];
        for (const b of next.blocks) {
            if (held.has(b.id) && currentNodes.has(b.id)) { nodes.push(currentNodes.get(b.id)!); continue; }
            const cur = currentNodes.get(b.id);
            if (cur && cur.attrs.head === b.head) { nodes.push(cur); continue; }
            const { node, shadow } = blockNode(schema, b);
            if (shadow) shadows.set(b.id, shadow);
            nodes.push(node);
        }
        for (const bid of currentNodes.keys()) if (!byId.has(bid) && !held.has(bid)) shadows.delete(bid);
        const newDoc = schema.nodes.doc.create(null, nodes);
        let tr = view.state.tr.replaceWith(0, view.state.doc.content.size, newDoc.content).setMeta(RECORDED, []).setMeta(decoKey, true);
        // Restore the caret by block and scalar offset.
        if (caretBid) {
            let pos = 0;
            tr.doc.forEach((n, p) => { if (n.attrs.bid === caretBid) pos = p + 1 + Math.min(n.content.size, caretOff); });
            if (pos) tr = tr.setSelection(TextSelection.create(tr.doc, pos));
        }
        view.dispatch(tr);
    }

    function featureBlockAt(pos: number): string | null {
        const $p = view.state.doc.resolve(Math.min(pos, view.state.doc.content.size));
        return $p.depth >= 1 ? ($p.node(1).attrs.bid as string) : null;
    }

    function scalarCaret(pos: number): number {
        const $p = view.state.doc.resolve(pos);
        return $p.depth >= 1 ? $p.parentOffset : 0;
    }

    return {
        view,
        adopt,
        ack,
        flush,
        featureAt,
        deleteFeature: fid => {
            const binds = projection.bindings.by_feature[fid] ?? [];
            const g = deleteFeature(view.state, env, fid, binds);
            if (g) view.dispatch(g.tr);
        },
        moveBlock: (bid, after, delta = 0) => { const g = moveBlock(view.state, env, bid, after, delta); if (g) view.dispatch(g.tr); },
        projection: () => projection,
        destroy: () => { flush(); view.destroy(); },
    };
}

export type { Shadow, Schema };
