/**
 * adoption.ts: bringing the page in line with a projection (docs/concurrency.md,
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
 * projection's order and the transaction is tagged `REPLACED`. Either way each
 * end of the selection is put back after the character it sat after, found by
 * identity.
 *
 * A block whose op the daemon refused is re-read once nothing of it is in
 * flight (`Outbox.reloadable`). A projection of a different `store` comes from
 * a daemon that rebuilt its store: every block without an op in flight is
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

export type Adopt = (next: Projection) => void;

type End = { bid: string; off: number; ref: CharRef | undefined };

export function adopter(view: EditorView, shadows: Map<string, Shadow>, outbox: Outbox, paints: Paints, shown: Shown): Adopt {
    const schema = view.state.schema;

    /**
     * Mark the blocks whose paints this projection can have changed. A block's
     * paints come from its own projected fields and from its feature's phase,
     * editors and claims, so most projections touch few. A reorder touches
     * every block.
     *
     * The plans list is not compared. That rests on the applier touching every
     * block a plan's ops landed in when the plan gets a verdict, which moves
     * those blocks' `head`s and puts them in `changed`.
     */
    function markPaints(prev: Projection, next: Projection, changed: Set<string>, reordered: boolean): void {
        if (reordered || paints.allMarked) {
            paints.mark();
            return;
        }
        const features = new Set<string>();
        for (const fid of new Set([...Object.keys(prev.phases), ...Object.keys(next.phases)])) {
            if (prev.phases[fid] !== next.phases[fid] || !sameJson(prev.editors?.[fid], next.editors?.[fid])) features.add(fid);
        }
        for (const fid of new Set([...Object.keys(prev.claims), ...Object.keys(next.claims)])) {
            if (!sameJson(prev.claims[fid], next.claims[fid])) features.add(fid);
        }
        for (const bid of changed) paints.mark(bid);
        if (features.size) for (const [bid, fid] of paints.featureOf) if (fid && features.has(fid)) paints.mark(bid);
    }

    /** A selection end as identity: its block, its offset there, and the character before it. */
    function endAt(pos: number): End | null {
        const $p = view.state.doc.resolve(pos);
        if ($p.depth < 1) return null;
        const block = $p.node(1), bid = block.attrs.bid as string;
        const off = scalarOffset(block, $p.parentOffset);
        return { bid, off, ref: off > 0 ? shadows.get(bid)?.refs[off - 1] : undefined };
    }

    /** Where an end is in `doc`: after the same character, at the start when it
     *  was at the start, or null. With `loose`, at its old offset when the
     *  character is gone. */
    function posOf(doc: PMNode, e: End | null, loose: boolean): number | null {
        if (!e) return null;
        let k = e.off;
        if (k > 0) {
            const i = e.ref ? shadows.get(e.bid)?.indexOf(e.ref) ?? -1 : -1;
            if (i >= 0) k = i + 1;
            else if (!loose) return null;
        }
        let pos: number | null = null;
        doc.forEach((n, p) => { if (n.attrs.bid === e.bid) pos = Math.min(p + 1 + unitOffset(n, k), doc.content.size); });
        return pos;
    }

    /** Put the selection back. A range comes back when both its ends still
     *  exist; otherwise the caret goes where the head was. */
    function restoreSelection(tr: Transaction, anchor: End | null, head: End | null): Transaction {
        const a = posOf(tr.doc, anchor, false), h = posOf(tr.doc, head, false);
        if (a !== null && h !== null) return tr.setSelection(TextSelection.create(tr.doc, a, h));
        const at = posOf(tr.doc, head, true);
        return at === null ? tr : tr.setSelection(TextSelection.create(tr.doc, at));
    }

    return function adopt(next) {
        // The host reads the daemon's files asynchronously and a watcher can
        // fire twice for one write, so a projection can arrive after a newer
        // one. Within one store `head` only grows; a rebuilt store starts over.
        const rebuilt = next.store !== shown.projection.store;
        if (!rebuilt && next.head < shown.projection.head) return;
        outbox.flush();
        if (rebuilt) outbox.rebuilt();
        outbox.covered(next.frontier);
        // Re-read every block after a rebuild, and a refused block once it is free.
        // A block with an op in flight stays held either way.
        const held = outbox.held(next.frontier);
        const force = rebuilt ? new Set<string>() : outbox.reloadable(next.frontier);
        if (rebuilt) view.state.doc.forEach(n => { if (!held.has(n.attrs.bid)) force.add(n.attrs.bid as string); });
        const prev = shown.projection;
        shown.projection = next;
        const current: Array<{ bid: string; head: number }> = [];
        view.state.doc.forEach(n => current.push({ bid: n.attrs.bid as string, head: force.has(n.attrs.bid) ? -1 : n.attrs.head as number }));
        const plan = adoptionPlan(current, next, held);
        if (!plan.length) {
            markPaints(prev, next, new Set(), false);
            view.dispatch(view.state.tr.setMeta(REPAINT, true));
            return;
        }
        const anchor = endAt(view.state.selection.anchor), head = endAt(view.state.selection.head);
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
            markPaints(prev, next, changed, rebuilt);
            tr = tr.setMeta(RECORDED, []).setMeta(REPAINT, true);
            view.dispatch(restoreSelection(tr, anchor, head));
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
        markPaints(prev, next, changed, rebuilt || order.join(' ') !== page.join(' '));
        const newDoc = schema.nodes.doc.create(null, order.map(id => nodeFor.get(id)!));
        const tr = view.state.tr.replaceWith(0, view.state.doc.content.size, newDoc.content)
            .setMeta(RECORDED, []).setMeta(REPLACED, true).setMeta(REPAINT, true);
        view.dispatch(restoreSelection(tr, anchor, head));
    };
}

/** Deep equality for the small projection slices the paints read. Every
 *  projection is a fresh parse, so identity never holds. */
function sameJson(a: unknown, b: unknown): boolean {
    return a === b || JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}
