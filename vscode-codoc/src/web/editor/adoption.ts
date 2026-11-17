/**
 * adoption.ts — bringing the page in line with a projection (docs/concurrency.md,
 * "The editor's side").
 *
 * The projection is the truth for every block the person is not holding
 * (outbox.ts says which those are). A held block keeps its node, its place and
 * its shadow. Every other block whose `head` moved is rebuilt from the
 * projection, and blocks the projection added, dropped or re-ranked are
 * inserted, removed or moved.
 *
 * When only words changed, the changed blocks are replaced in place. When a
 * block arrived, left or moved, the whole document is rebuilt in the
 * projection's order and the transaction is tagged `REPLACED`. Either way the
 * caret is put back after the character it sat after, found by identity.
 *
 * A block whose op the daemon refused is re-read once nothing of it is in
 * flight (`Outbox.reloadable`). A projection of a different `store` comes from
 * a daemon that rebuilt its store: every hold is dropped and every block is
 * re-read, because positions and frontiers of the old store say nothing about
 * the new one.
 */
import type { Node as PMNode } from '@tiptap/pm/model';
import { TextSelection, type Transaction } from '@tiptap/pm/state';
import type { EditorView } from '@tiptap/pm/view';

import { adoptionPlan, blockNode, flowBlocks } from '../calc/adopt';
import type { CharRef, Projection } from '../calc/projection';
import { scalarOffset } from '../calc/recorder';
import type { Shadow } from '../calc/shadow';
import { RECORDED, REPLACED } from './commands';
import { REPAINT, unitOffset, type Paints, type Shown } from './decorations';
import type { Outbox } from './outbox';

export type Adopt = (next: Projection, realizing?: ReadonlySet<string>) => void;

export function adopter(view: EditorView, shadows: Map<string, Shadow>, outbox: Outbox, paints: Paints, shown: Shown): Adopt {
    const schema = view.state.schema;

    /**
     * Mark the blocks whose paints this projection can have changed. A block's
     * paints come from its own projected fields and from its feature's phase,
     * claims and watermarks, so most projections touch few. A reorder or a
     * change to the realizing set touches every block.
     *
     * The plans list is not compared. That rests on the applier touching every
     * block a plan's ops landed in when the plan gets a verdict, which moves
     * those blocks' `head`s and puts them in `changed`.
     */
    function markPaints(prev: Projection, next: Projection, changed: Set<string>, reordered: boolean, wasRealizing: ReadonlySet<string>): void {
        const realizing = shown.realizing;
        if (reordered || paints.allMarked
            || wasRealizing.size !== realizing.size || [...realizing].some(f => !wasRealizing.has(f))) {
            paints.mark();
            return;
        }
        const features = new Set<string>();
        const pw = prev.watermarks ?? {}, nw = next.watermarks ?? {};
        for (const fid of new Set([...Object.keys(prev.phases), ...Object.keys(next.phases)])) {
            if (prev.phases[fid] !== next.phases[fid]) features.add(fid);
        }
        for (const fid of new Set([...Object.keys(prev.claims), ...Object.keys(next.claims)])) {
            if (!sameJson(prev.claims[fid], next.claims[fid])) features.add(fid);
        }
        for (const fid of new Set([...Object.keys(pw), ...Object.keys(nw)])) {
            if (!sameJson(pw[fid], nw[fid])) features.add(fid);
        }
        for (const bid of changed) paints.mark(bid);
        if (features.size) for (const [bid, fid] of paints.featureOf) if (fid && features.has(fid)) paints.mark(bid);
    }

    /** Put the caret back in the block it was in, after the character it sat after. */
    function restoreCaret(tr: Transaction, bid: string | null, off: number, ref: CharRef | undefined): Transaction {
        if (!bid) return tr;
        const at = ref ? shadows.get(bid)?.indexOf(ref) ?? -1 : -1;
        const want = at >= 0 ? at + 1 : off;
        let pos = 0;
        tr.doc.forEach((n, p) => { if (n.attrs.bid === bid) pos = p + 1 + unitOffset(n, want); });
        return pos ? tr.setSelection(TextSelection.create(tr.doc, Math.min(pos, tr.doc.content.size))) : tr;
    }

    return function adopt(next, nowRealizing = new Set()) {
        // The host reads the daemon's files asynchronously and a watcher can
        // fire twice for one write, so a projection can arrive after a newer
        // one. Within one store `head` only grows; a rebuilt store starts over.
        const rebuilt = next.store !== shown.projection.store;
        if (!rebuilt && next.head < shown.projection.head) return;
        outbox.flush();
        if (rebuilt) outbox.rebuilt();
        const force = new Set<string>();
        if (rebuilt) view.state.doc.forEach(n => force.add(n.attrs.bid as string));
        else for (const bid of outbox.reloadable(next.frontier)) force.add(bid);
        const prev = shown.projection, wasRealizing = shown.realizing;
        shown.projection = next;
        shown.realizing = nowRealizing;
        const current: Array<{ bid: string; head: number }> = [];
        view.state.doc.forEach(n => current.push({ bid: n.attrs.bid as string, head: force.has(n.attrs.bid) ? -1 : n.attrs.head as number }));
        const held = outbox.held(next.frontier, force);
        const plan = adoptionPlan(current, next, held);
        if (!plan.length) {
            markPaints(prev, next, new Set(), false, wasRealizing);
            view.dispatch(view.state.tr.setMeta(REPAINT, true));
            return;
        }
        const $caret = view.state.doc.resolve(view.state.selection.from);
        const caretBlock = $caret.depth >= 1 ? $caret.node(1) : null;
        const caretBid = caretBlock ? caretBlock.attrs.bid as string : null;
        const caretOff = caretBlock ? scalarOffset(caretBlock, $caret.parentOffset) : 0;
        const caretRef = caretBid && caretOff > 0 ? shadows.get(caretBid)?.refs[caretOff - 1] : undefined;
        const changed = new Set<string>();

        // Only words changed: the structure is already right, so replace each
        // changed block where it stands. This is the common adoption (another
        // actor's settle, an ack that carries a keystroke) and it keeps the
        // decoration set, which a whole-document replace would rebuild.
        if (plan.every(st => st.kind === 'replace')) {
            let tr = view.state.tr;
            for (const st of plan) {
                const b = next.blocks.find(x => x.id === st.bid);
                if (!b) continue;
                const { node, shadow } = blockNode(schema, b);
                if (shadow) shadows.set(b.id, shadow);
                let at = -1;
                tr.doc.forEach((n, pos) => { if (n.attrs.bid === b.id) at = pos; });
                if (at < 0) continue;
                tr = tr.replaceWith(at, at + tr.doc.nodeAt(at)!.nodeSize, node);
                changed.add(b.id);
            }
            markPaints(prev, next, changed, rebuilt, wasRealizing);
            tr = tr.setMeta(RECORDED, []).setMeta(REPAINT, true);
            view.dispatch(restoreCaret(tr, caretBid, caretOff, caretRef));
            return;
        }

        // Rebuild the document in the projection's order, keeping held blocks' nodes.
        const currentNodes = new Map<string, PMNode>();
        view.state.doc.forEach(n => currentNodes.set(n.attrs.bid as string, n));
        const order: string[] = [];
        const nodeFor = new Map<string, PMNode>();
        for (const b of flowBlocks(next)) {
            const cur = currentNodes.get(b.id);
            // Held and absent: the person removed it and the projection predates that.
            if (held.has(b.id) && !cur) continue;
            order.push(b.id);
            if (cur?.attrs.head !== b.head || force.has(b.id)) changed.add(b.id);
            if (cur && (held.has(b.id) || (cur.attrs.head === b.head && !force.has(b.id)))) { nodeFor.set(b.id, cur); continue; }
            const { node, shadow } = blockNode(schema, b);
            if (shadow) shadows.set(b.id, shadow);
            nodeFor.set(b.id, node);
        }
        // A held block the projection does not know keeps the place the page
        // gave it, after the block it follows now.
        const page = [...currentNodes.keys()];
        page.forEach((bid, i) => {
            if (nodeFor.has(bid) || !held.has(bid)) return;
            nodeFor.set(bid, currentNodes.get(bid)!);
            const at = i > 0 ? order.indexOf(page[i - 1]) : -1;
            order.splice(at >= 0 ? at + 1 : 0, 0, bid);
        });
        for (const bid of currentNodes.keys()) if (!nodeFor.has(bid)) { shadows.delete(bid); outbox.forget(bid); }
        markPaints(prev, next, changed, rebuilt || order.join(' ') !== page.join(' '), wasRealizing);
        const newDoc = schema.nodes.doc.create(null, order.map(id => nodeFor.get(id)!));
        const tr = view.state.tr.replaceWith(0, view.state.doc.content.size, newDoc.content)
            .setMeta(RECORDED, []).setMeta(REPLACED, true).setMeta(REPAINT, true);
        view.dispatch(restoreCaret(tr, caretBid, caretOff, caretRef));
    };
}

/** Deep equality for the small projection slices the paints read. Every
 *  projection is a fresh parse, so identity never holds. */
function sameJson(a: unknown, b: unknown): boolean {
    return a === b || JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}
