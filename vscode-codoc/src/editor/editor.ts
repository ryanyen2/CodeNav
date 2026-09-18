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
import { EditorView, type DecorationSet } from '@tiptap/pm/view';
import { keymap } from '@tiptap/pm/keymap';
import { baseKeymap } from '@tiptap/pm/commands';
import { InputRule, inputRules } from '@tiptap/pm/inputrules';

import { adoptionPlan, blockNode, docFrom, flowBlocks } from '../calc/adopt';
import { inFrontier, paintsFor, type Paint } from '../calc/claims';
import { NO_WATERMARKS, nfc, type Ack, type OpBody, type Projection, type Submission } from '../calc/projection';
import { Recorder, reconcile, scalarOffset } from '../calc/recorder';
import type { Shadow } from '../calc/shadow';
import { GESTURE_CIDS, RECORDED, REPLACED, deleteFeature, deleteSelection, headingFromRule, mergeBackward, mergeForward, moveBlock, pasteBlocks, relevel, selectedBlocks, splitBlock, type Env } from './commands';
import { blockRanges, buildDecorations, decorationsFor, unitOffset } from './decorations';
import { dragPlugin } from './drag';
import { FindWidget, findPlugin } from './find';
import { Menus } from './menus';
import { codocSchema } from './schema';

export interface EditorHost {
    /** Append submissions to the inbox (the host process writes the file). */
    submit(subs: Submission[]): void;
    /** A heading was clicked / the caret entered a feature. */
    onFeature?(fid: string | null): void;
    /** Open a code location (a cite mark). */
    openCite?(file: string, symbol: string): void;
    /** The daemon refused one of this editor's ops; the block was re-adopted. */
    onConflict?(bid: string, conflict: string): void;
}

export interface EditorHandle {
    view: EditorView;
    adopt(proj: Projection, realizing?: ReadonlySet<string>): void;
    ack(acks: Ack[]): void;
    flush(): void;
    featureAt(pos: number): string | null;
    /** Show who wrote each run of words (the History stance). */
    setHistory(on: boolean): void;
    find(replace: boolean): void;
    /** The current selection as a comment anchor: block, words, character refs. */
    selectionAnchor(): { bid: string; text: string; start: unknown; end: unknown } | null;
    deleteFeature(fid: string): void;
    moveBlock(bid: string, afterBid: string | 'START', levelDelta?: number): void;
    projection(): Projection | null;
    destroy(): void;
}

const decoKey = new PluginKey('codoc-paints');
const SETTLE_MS = 350;

let seq = 0;
// A per-page prefix: two windows of the same client mint client ids from their
// own counters, and without it two of them in one millisecond collide — the
// daemon's per-client dedup then drops the second window's op as a replay.
const tab = Math.random().toString(36).slice(2, 8);
const newBid = () => `b-${Math.random().toString(16).slice(2, 14).padEnd(12, '0')}`;
const newCid = () => `ide-${tab}-${Date.now().toString(36)}-${(seq++).toString(36)}`;
const newBatch = () => `t-${Math.random().toString(16).slice(2, 14)}`;

export function mountEditor(container: HTMLElement, proj: Projection, host: EditorHost): EditorHandle {
    const schema = codocSchema();
    const { doc, shadows } = docFrom(schema, proj);
    let projection: Projection = proj;
    let realizing: ReadonlySet<string> = new Set();
    let history = false;
    /** Client ids awaiting an ack, by block. */
    const unacked = new Map<string, Set<string>>();
    /** Submissions not yet flushed. */
    let pending: Submission[] = [];
    let batch = newBatch();
    let settleTimer: number | undefined;
    const env: Env = { shadows, nextBid: newBid, nextCid: newCid };
    const recorder = new Recorder(shadows, newCid);

    const paints = new PluginKey<DecorationsState>('codoc-decos');
    interface DecorationsState { set: DecorationSet }

    // The set is KEPT, mapped through each change, and patched where the paints
    // moved. A keystroke changes one block; rebuilding three thousand inline
    // decorations for it — and again for the ack, and again for the projection
    // that follows — was most of what typing in a large tree cost.
    const decoPlugin = new Plugin<DecorationsState>({
        key: paints,
        state: {
            init: (_c, state) => ({ set: buildDecorations(state.doc, paintMap(state.doc), projection.phases, realizing) }),
            apply(tr, prev, _old, state) {
                if (!tr.docChanged && !tr.getMeta(decoKey)) return prev;
                // Which feature a block belongs to is WHERE IT SITS, so a block
                // arriving or leaving can change any later block's paints. The
                // transaction is what knows, and it is known here — deciding it
                // after `applyTransaction` set the flag for the NEXT one, so a
                // drag left the moved heading with no marker until a keystroke.
                const dirty = paintsAll || (tr.docChanged && !sameStructure(tr.before, tr.doc))
                    ? null : new Set(paintsDirty);
                const map = paintMap(state.doc);          // clears the dirty set
                if (dirty === null) {
                    return { set: buildDecorations(state.doc, map, projection.phases, realizing) };
                }
                let set = prev.set.map(tr.mapping, state.doc);
                if (dirty.size) {
                    for (const r of blockRanges(state.doc, dirty)) set = set.remove(set.find(r.from, r.to));
                    set = set.add(state.doc, decorationsFor(state.doc, map, projection.phases, realizing, dirty));
                }
                return { set };
            },
        },
        props: { decorations: state => paints.getState(state)?.set ?? null },
    });

    /** Paints per block. The MAP is kept, not rebuilt: every keystroke and every
     *  ack redraws the decoration set, and walking three thousand blocks to
     *  build a key for each is the difference between typing and waiting. A
     *  keystroke changes one block's unacked count, so it repaints one block;
     *  everything that can change them all — a projection, the realizing set,
     *  the History stance, a block appearing or leaving — says so. */
    const paintsOut = new Map<string, Paint[]>();
    const paintFid = new Map<string, string | null>();
    let paintsAll = true;
    const paintsDirty = new Set<string>();

    function repaint(bid?: string): void {
        if (bid === undefined) paintsAll = true;
        else paintsDirty.add(bid);
    }

    function paintsOf(bid: string, fid: string | null): void {
        const pb = projection.blocks.find(b => b.id === bid);
        if (!pb) { paintsOut.delete(bid); return; }
        paintsOut.set(bid, paintsFor(pb, projection, fid, unackedOps(bid), watermarksFor(fid), history));
    }

    function paintMap(d: PMNode): Map<string, Paint[]> {
        if (paintsAll) {
            paintsOut.clear();
            paintFid.clear();
            const byId = new Map(projection.blocks.map(b => [b.id, b]));
            let fid: string | null = null;
            d.forEach(block => {
                const bid = block.attrs.bid as string | null;
                if (!bid) return;
                if (block.type.name === 'heading') fid = bid;
                paintFid.set(bid, fid);
                const pb = byId.get(bid);
                if (pb) paintsOut.set(bid, paintsFor(pb, projection, fid, unackedOps(bid), watermarksFor(fid), history));
            });
        } else {
            for (const bid of paintsDirty) if (paintFid.has(bid)) paintsOf(bid, paintFid.get(bid) ?? null);
        }
        paintsAll = false;
        paintsDirty.clear();
        return paintsOut;
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
        if (tagged) {
            // An EMPTY list is not a gesture: adoption and the NFC fix-up carry
            // the tag to pass the structural guard, and holding what they moved
            // would leave every block a projection inserts or removes waiting
            // for an ack nobody will send — for the life of the page.
            if (tagged.length) queue(tagged, (tr.getMeta(GESTURE_CIDS) as string[]) ?? [], bornOrGone(tr));
            return;
        }
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

    /** The blocks a gesture changed the existence of on the page: there before
     *  and gone after, or new after. They are held with everything the ops
     *  name, because a projection that predates the gesture still lists what it
     *  removed and still lacks what it added (09-editor §5). */
    function bornOrGone(tr: Transaction): string[] {
        const before = new Set<string>(), after = new Set<string>();
        tr.before.forEach(n => { if (n.attrs.bid) before.add(n.attrs.bid as string); });
        tr.doc.forEach(n => { if (n.attrs.bid) after.add(n.attrs.bid as string); });
        return [...before, ...after].filter(b => before.has(b) !== after.has(b));
    }

    function queue(ops: OpBody[], cids: string[], holds: string[] = []): void {
        let ci = 0;
        let first = '';
        const hold = (bid: unknown, cid: string) => {
            if (typeof bid !== 'string' || !bid) return;
            unacked.set(bid, (unacked.get(bid) ?? new Set()).add(cid));
            repaint(bid);
        };
        for (const op of ops) {
            const cid = op.kind === 'text.insert' ? (cids[ci++] ?? newCid()) : newCid();
            first ||= cid;
            // Every block the op names, not just its target: a merge is about
            // the block that dies as much as the one that survives, and a split
            // about the one it mints.
            for (const bid of [op.target.bid, op.target.into, op.target.fid, op.payload?.new]) hold(bid, cid);
            pending.push({ cid, batch, ...op });
        }
        for (const bid of holds) hold(bid, first || newCid());
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
        const refused = new Map<string, string>();
        const released = new Map<string, string[]>();      // block → the ops its acks named
        for (const a of acks) {
            const waiting: string[] = [];
            for (const [bid, set] of unacked) {
                if (set.delete(a.cid)) {
                    waiting.push(bid);
                    repaint(bid);
                    if (a.conflict) refused.set(bid, a.conflict);
                    if (a.op) released.set(bid, [...(released.get(bid) ?? []), a.op]);
                    if (set.size === 0) unacked.delete(bid);
                }
            }
            // Only the blocks that were waiting on this cid hold its characters.
            if (a.op && !a.conflict) for (const bid of waiting) shadows.get(bid)?.resolve(a.cid, a.op);
        }
        if (refused.size) {
            // The op did nothing in the store: the block's shadow lies. Take the
            // projection's word for that block again (09-editor §5).
            for (const [bid, code] of refused) host.onConflict?.(bid, code);
            adopt(projection, realizing, new Set(refused.keys()));
            return;
        }
        const stale = caughtUp(released);
        if (stale.size) { adopt(projection, realizing, stale); return; }
        view.dispatch(view.state.tr.setMeta(decoKey, true));
    }

    /** The blocks this ack released whose projected `head` the page has not read.
     *
     *  `adopt` leaves a held block alone — the page is right about it and the
     *  projection may predate the gesture. Nothing re-read it afterwards, so
     *  another actor's letter in the paragraph you are typing in, an agent's
     *  plan there, and your own Backspace coming back as the plan's
     *  strike-through all waited for an unrelated publish. On the hub they
     *  waited forever: its feed sends the projection before the acks, so the
     *  hold is always still on when the projection lands.
     *
     *  The frontier is what makes this safe. A projection whose frontier covers
     *  every op the acks named already CONTAINS this block's gesture, so its
     *  word is newer than the page's. One that does not predates the gesture
     *  and adopting it would undo the person's typing under their hands. */
    function caughtUp(released: ReadonlyMap<string, string[]>): Set<string> {
        const out = new Set<string>();
        if (!released.size) return out;
        const heads = new Map(projection.blocks.map(b => [b.id, b.head]));
        view.state.doc.forEach(n => {
            const bid = n.attrs.bid as string;
            const ops = released.get(bid);
            if (!ops || (unacked.get(bid)?.size ?? 0) > 0) return;
            if (!ops.every(op => inFrontier(op, projection.frontier))) return;
            if (heads.get(bid) !== undefined && heads.get(bid) !== n.attrs.head) out.add(bid);
        });
        return out;
    }

    // ── gestures ───────────────────────────────────────────────────────────
    const run = (g: ReturnType<typeof splitBlock>) => { if (!g) return false; view.dispatch(g.tr); return true; };

    /** A selection that spans blocks is deleted as ops, or refused when it
     *  touches a heading: ProseMirror's own commands would take blocks out of
     *  the document with nothing recorded. Returns false when there is nothing
     *  selected, so the caret gestures run as usual. */
    const cutSelection = (state: EditorState): boolean | 'refused' => {
        if (state.selection.empty) return false;
        const parts = selectedBlocks(state);
        if (parts.length < 2) return false;                      // one block: the recorder handles it
        const g = deleteSelection(state, env);
        if (!g) {
            host.onConflict?.('', parts.some(p => p.node.type.name === 'heading')
                ? 'a selection across a heading is not a delete; use Delete feature'
                : 'a selection across a block that is not prose is not a delete');
            return 'refused';
        }
        view.dispatch(g.tr);
        return true;
    };

    const keys = keymap({
        Enter: state => {
            const cut = cutSelection(state);
            if (cut === 'refused') return true;
            if (cut) return run(splitBlock(view.state, env)) || true;
            if (state.selection.empty) return run(splitBlock(state, env));
            // One block with something selected: the delete is ordinary text the
            // recorder speaks for, the split is a gesture. Two dispatches in that
            // order, because together they are a structural change no single
            // gesture describes and the guard would refuse the pair.
            view.dispatch(state.tr.deleteSelection());
            return run(splitBlock(view.state, env)) || true;
        },
        Backspace: state => {
            const cut = cutSelection(state);
            if (cut) return true;
            const g = mergeBackward(state, env);
            if (g) return run(g);
            return refuseHeadingMerge(state, 'back');
        },
        Delete: state => {
            const cut = cutSelection(state);
            if (cut) return true;
            const g = mergeForward(state, env);
            if (g) return run(g);
            return refuseHeadingMerge(state, 'forward');
        },
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
        const g = headingFromRule(state, env, match);
        return g ? g.tr : null;
    });

    let menus: Menus | null = null;
    const state = EditorState.create({
        schema,
        doc,
        plugins: [
            new Plugin({ props: { handleKeyDown: (_v, ev) => menus?.handleKeyDown(ev) ?? false } }),
            // Structure changes only through a recorded gesture. A transaction
            // that would add, drop, duplicate or un-id a top-level block
            // without one is refused: the recorder can only speak for text
            // inside blocks it knows, so anything else would leave the store
            // and the page saying different things (09-editor §4 rule 1).
            new Plugin({ filterTransaction: (tr, st) => !!tr.getMeta(RECORDED) || !tr.docChanged || sameStructure(st.doc, tr.doc) }),
            // The store keeps NFC and so does the shadow, so the document must
            // too. A decomposed keystroke (some IMEs, a macOS paste) otherwise
            // leaves the page one scalar longer than the shadow and every
            // anchor after it off by one — `anchorBefore` then throws inside
            // `dispatch`, the DOM keeps the character and the state does not.
            // The ops already said NFC, so this composes the page and records
            // nothing.
            new Plugin({ appendTransaction: composeNFC }),
            keys, inputRules({ rules: [headingRule] }), keymap(baseKeymap), decoPlugin, findPlugin(),
            dragPlugin((bid, after) => { const g = moveBlock(view.state, env, bid, after); if (g) view.dispatch(g.tr); }),
        ],
    });

    const view = new EditorView(container, {
        state,
        nodeViews: { kindBlock: node => kindView(node) },
        dispatchTransaction(tr) {
            // Record what the page actually took, never what it was asked to
            // take. `filterTransaction` refuses a structural change no gesture
            // spoke for, and a refused transaction must leave no trace: the
            // recorder used to run first, so a refused gesture still advanced
            // the shadows and queued deletes for words still on screen, and the
            // next projection took them away (09-editor §4 rule 1).
            const { state: next, transactions } = view.state.applyTransaction(tr);
            for (const applied of transactions) record(applied);
            view.updateState(next);
            if (tr.selectionSet || tr.docChanged) { host.onFeature?.(featureAt(next.selection.from)); menus?.update(); }
        },
        handlePaste(_v, _event, slice) {
            const g = pasteBlocks(view.state, env, slice);
            if (!g) return false;                       // inline paste: the recorder records it
            view.dispatch(g.tr);
            return true;
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

    menus = new Menus(view, env, () => projection, container);
    const finder = new FindWidget(view, container);

    function featureAt(pos: number): string | null {
        const $p = view.state.doc.resolve(Math.min(pos, view.state.doc.content.size));
        if ($p.depth < 1) return null;
        for (let i = $p.index(0); i >= 0; i--) {
            const n = view.state.doc.child(i);
            if (n.type.name === 'heading') return n.attrs.bid as string;
        }
        return null;
    }

    /** Compose any decomposed run these transactions INSERTED. Only what they
     *  inserted: the store records NFC per insert, so `e` and U+0301 typed as
     *  two ops are two characters everywhere — in the store, the projection and
     *  the shadow — and composing them because a paste elsewhere happened to
     *  carry a decomposed run would shorten a paragraph the transaction never
     *  touched and record nothing. The scan is over the changed ranges, so an
     *  ordinary keystroke pays one NFC of one character. */
    function composeNFC(trs: readonly Transaction[], _old: EditorState, next: EditorState): Transaction | null {
        const ranges: Array<[number, number]> = [];
        trs.forEach((t, i) => {
            t.steps.forEach((step, j) => {
                step.getMap().forEach((_a, _b, fromB, toB) => {
                    let from = fromB, to = toB;
                    const rest = t.mapping.slice(j + 1);
                    from = rest.map(from, -1);
                    to = rest.map(to, 1);
                    for (const later of trs.slice(i + 1)) { from = later.mapping.map(from, -1); to = later.mapping.map(to, 1); }
                    if (to > from) ranges.push([from, to]);
                });
            });
        });
        if (!ranges.length) return null;
        // The unit is the inserted RUN, composed on its own — the same unit the
        // shadow and the store use. Composing the text node it landed in would
        // fold a neighbour the transaction never touched: `e` and U+0301 typed
        // as two keystrokes are two characters everywhere, on purpose.
        const size = next.doc.content.size;
        const fixes: Array<{ from: number; to: number; node: PMNode }> = [];
        for (const [from, to] of ranges) {
            const a = Math.max(0, from), b = Math.min(size, to);
            if (b <= a) continue;
            const $a = next.doc.resolve(a), $b = next.doc.resolve(b);
            if (!$a.parent.isTextblock || $a.parent !== $b.parent) continue;   // not one run of text
            const raw = next.doc.textBetween(a, b);
            const composed = nfc(raw);
            if (composed === raw) continue;
            const marks = next.doc.nodeAt(a)?.marks ?? [];
            fixes.push({ from: a, to: b, node: next.schema.text(composed, marks) });
        }
        if (!fixes.length) return null;
        let tr = next.tr;
        for (const f of fixes.sort((x, y) => y.from - x.from)) tr = tr.replaceWith(f.from, f.to, f.node);
        return tr.setMeta(RECORDED, []).setMeta('addToHistory', false);
    }

    // ── adoption ───────────────────────────────────────────────────────────
    function adopt(next: Projection, nowRealizing: ReadonlySet<string> = new Set(), force: ReadonlySet<string> = new Set()): void {
        flush();
        projection = next;
        realizing = nowRealizing;
        repaint();                     // a new projection can change any block's paints
        const current: Array<{ bid: string; head: number }> = [];
        view.state.doc.forEach(n => current.push({ bid: n.attrs.bid as string, head: force.has(n.attrs.bid) ? -1 : n.attrs.head as number }));
        const held = new Set([...unacked.keys()].filter(b => (unacked.get(b)?.size ?? 0) > 0 && !force.has(b)));
        const plan = adoptionPlan(current, next, held);
        if (!plan.length) { view.dispatch(view.state.tr.setMeta(decoKey, true)); return; }
        const sel = view.state.selection;
        const caretBid = featureBlockAt(sel.from);
        const caretOff = caretBid ? scalarCaret(sel.from) : 0;
        // The caret by character identity (§6): the character it sits after
        // keeps its place through a replacement, whatever changed before it.
        const caretRef = caretBid && caretOff > 0 ? shadows.get(caretBid)?.refs[caretOff - 1] : undefined;
        // Rebuild the document in the projection's order, keeping held blocks' nodes.
        const currentNodes = new Map<string, PMNode>();
        view.state.doc.forEach(n => currentNodes.set(n.attrs.bid as string, n));
        // Held means: the page is right about this block, both that it is there
        // and what it says. A projection older than the person's own gesture
        // still lists the paragraph they merged away and still lacks the one
        // they split off, so adopting it wholesale would undo the gesture under
        // their hands and then undo the undo on the next publish.
        const order: string[] = [];
        const nodeFor = new Map<string, PMNode>();
        for (const b of flowBlocks(next)) {
            const cur = currentNodes.get(b.id);
            if (held.has(b.id) && !cur) continue;                        // the page removed it
            order.push(b.id);
            if (cur && (held.has(b.id) || (cur.attrs.head === b.head && !force.has(b.id)))) { nodeFor.set(b.id, cur); continue; }
            const { node, shadow } = blockNode(schema, b);
            if (shadow) shadows.set(b.id, shadow);
            nodeFor.set(b.id, node);
        }
        // A held block the projection does not know about keeps the place the
        // page gave it, after the block it currently follows — not the top.
        const page = [...currentNodes.keys()];
        page.forEach((bid, i) => {
            if (nodeFor.has(bid) || !held.has(bid)) return;
            nodeFor.set(bid, currentNodes.get(bid)!);
            const at = i > 0 ? order.indexOf(page[i - 1]) : -1;
            order.splice(at >= 0 ? at + 1 : 0, 0, bid);
        });
        const nodes = order.map(id => nodeFor.get(id)!);
        for (const bid of currentNodes.keys()) if (!nodeFor.has(bid)) shadows.delete(bid);
        const newDoc = schema.nodes.doc.create(null, nodes);
        let tr = view.state.tr.replaceWith(0, view.state.doc.content.size, newDoc.content)
            .setMeta(RECORDED, []).setMeta(REPLACED, true).setMeta(decoKey, true);
        // Restore the caret by block and scalar offset.
        if (caretBid) {
            const moved = caretRef ? shadows.get(caretBid)?.indexOf(caretRef) ?? -1 : -1;
            const off = moved >= 0 ? moved + 1 : caretOff;
            let pos = 0;
            tr.doc.forEach((n, p) => { if (n.attrs.bid === caretBid) pos = p + 1 + unitOffset(n, off); });
            if (pos) tr = tr.setSelection(TextSelection.create(tr.doc, Math.min(pos, tr.doc.content.size)));
        }
        view.dispatch(tr);
    }

    function featureBlockAt(pos: number): string | null {
        const $p = view.state.doc.resolve(Math.min(pos, view.state.doc.content.size));
        return $p.depth >= 1 ? ($p.node(1).attrs.bid as string) : null;
    }

    /** UTF-16 offset inside a block for a scalar offset. */
function scalarCaret(pos: number): number {
        const $p = view.state.doc.resolve(pos);
        return $p.depth >= 1 ? scalarOffset($p.node(1), $p.parentOffset) : 0;
    }

    return {
        view,
        adopt,
        ack,
        flush,
        featureAt,
        setHistory: on => { history = on; repaint(); view.dispatch(view.state.tr.setMeta(decoKey, true)); },
        find: replace => finder.open(replace),
        selectionAnchor: () => {
            const { from, to, empty } = view.state.selection;
            const $f = view.state.doc.resolve(from), $t = view.state.doc.resolve(to);
            if (empty || $f.depth !== 1 || $t.depth !== 1 || $f.parent !== $t.parent) return null;
            const bid = $f.parent.attrs.bid as string;
            const sh = shadows.get(bid);
            if (!sh) return null;
            const a = scalarOffset($f.parent, $f.parentOffset), b = scalarOffset($t.parent, $t.parentOffset);
            if (b <= a) return null;
            return { bid, text: sh.chars.slice(a, b).join(''), start: sh.ref(a), end: sh.ref(b - 1) };
        },
        deleteFeature: fid => {
            const g = deleteFeature(view.state, env, fid);
            if (g) view.dispatch(g.tr);
        },
        moveBlock: (bid, after, delta = 0) => { const g = moveBlock(view.state, env, bid, after, delta); if (g) view.dispatch(g.tr); },
        projection: () => projection,
        destroy: () => { flush(); view.destroy(); },
    };
}

export type { Shadow, Schema };

/** The document's block identities, in order, for the structural guard. */
function structure(doc: PMNode): string {
    const ids: string[] = [];
    doc.forEach(b => ids.push(`${b.type.name}:${b.attrs.bid ?? '?'}`));
    return ids.join('|');
}

function sameStructure(before: PMNode, after: PMNode): boolean {
    return structure(before) === structure(after);
}

/** A plugin kind rendered read-only from its projected fields: the kind name,
 *  each Text field's words, and the rest as `key: value`. */
function kindView(node: PMNode): { dom: HTMLElement } {
    const dom = document.createElement('div');
    dom.className = `codoc-kind codoc-kind-${node.attrs.kind}`;
    dom.dataset.bid = node.attrs.bid;
    dom.dataset.kind = node.attrs.kind;
    dom.contentEditable = 'false';
    const label = document.createElement('div');
    label.className = 'codoc-kind-label';
    label.textContent = String(node.attrs.kind);
    dom.append(label);
    const fields = (node.attrs.fields ?? {}) as Record<string, unknown>;
    for (const [k, v] of Object.entries(fields)) {
        const row = document.createElement('div');
        row.className = 'codoc-kind-field';
        const isText = v && typeof v === 'object' && 'runs' in (v as object);
        const value = isText ? String((v as { text: string }).text) : typeof v === 'string' ? v : JSON.stringify(v);
        if (!value) continue;
        const key = document.createElement('span');
        key.className = 'codoc-kind-key';
        key.textContent = k;
        const val = document.createElement(isText && k === 'source' ? 'pre' : 'span');
        val.className = 'codoc-kind-value';
        val.textContent = value;
        row.append(key, val);
        dom.append(row);
    }
    return { dom };
}
