/**
 * outbox.ts: ops on their way from the page to the daemon, and what the page
 * holds while they travel (docs/concurrency.md, "The editor's side").
 *
 * `record` turns every transaction the page took into ops: a gesture's own
 * ops when the transaction carries them (commands.ts), otherwise what the
 * recorder reads from its steps, with the per-block comparison as the fallback
 * for a step it cannot name. Ops wait `SETTLE_MS` after the last keystroke and
 * leave together as one batch, which is the undo unit.
 *
 * While an IME composition is open the browser rewrites the composed text on
 * every keystroke (n, ni, 你). Nothing is recorded then: the blocks it touches
 * wait in `composed`, and `settle` compares each with its shadow once the
 * composition has ended, so the composed text leaves as one insert.
 *
 * A block is **held** while the page knows more about it than a projection
 * does: one of its ops is still unanswered, its composition is not settled,
 * or the projection in hand predates the last op the daemon applied for it.
 * Adoption leaves held blocks alone. `held` is that one predicate. Without the
 * first a projection overwrites words the daemon has not seen. Without the
 * last, an older projection arriving after the ack takes the acked words away
 * again.
 */
import type { Node as PMNode } from '@tiptap/pm/model';
import type { Transaction } from '@tiptap/pm/state';

import { inFrontier } from '../calc/claims';
import { newBatch, newCid } from '../calc/ids';
import type { Ack, OpBody, Submission } from '../calc/projection';
import { Recorder, reconcile } from '../calc/recorder';
import type { Shadow } from '../calc/shadow';
import type { CardFields } from './card';
import { GESTURE_CIDS, RECORDED } from './commands';

const SETTLE_MS = 350;

export class Outbox {
    /** Client ids awaiting an ack, by block. */
    private readonly unacked = new Map<string, Set<string>>();
    /** Block → the newest op the daemon applied for it. */
    private readonly lastOp = new Map<string, string>();
    /** Submissions not yet sent. */
    private pending: Submission[] = [];
    private batch = newBatch();
    private timer: number | undefined;
    private readonly recorder: Recorder;
    /** Blocks an open or newly ended IME composition changed, not yet recorded. */
    private readonly composed = new Set<string>();
    /** Blocks one of whose ops the daemon refused, waiting to be re-read. */
    private readonly stale = new Set<string>();
    /** The ops the daemon applied for this page that no adopted projection covers yet. */
    readonly applied = new Set<string>();

    /** `onHoldChange` is told each block whose hold changed, so its paints can be redrawn. */
    /** `cards` holds the field shadows of card blocks (card.ts), which an ack resolves too. */
    constructor(private readonly shadows: Map<string, Shadow>,
                private readonly submit: (subs: Submission[]) => void,
                private readonly onHoldChange: (bid: string) => void,
                private readonly cards: CardFields = new Map()) {
        this.recorder = new Recorder(shadows, newCid);
    }

    /** Turn a transaction the page has applied into queued ops. `composing`
     *  says an IME composition is open. */
    record(tr: Transaction, composing = false): void {
        const tagged = tr.getMeta(RECORDED) as OpBody[] | undefined;
        if (tagged) {
            // An empty list is not a gesture. Adoption and the NFC fix-up carry
            // the tag only to pass the structural guard, and holding the blocks
            // they moved would wait for an ack nobody sends.
            if (tagged.length) this.queue(tagged, (tr.getMeta(GESTURE_CIDS) as string[]) ?? [], bornOrGone(tr));
            return;
        }
        if (!tr.docChanged) return;
        if (composing || this.composed.size) {
            // A change that ends a composition belongs to it too: some
            // browsers deliver the committed text after `compositionend`.
            for (const bid of this.touched(tr, tr.steps.map((_s, i) => i))) { this.composed.add(bid); this.onHoldChange(bid); }
            return;
        }
        const rec = this.recorder.record(tr.steps, tr.docs);
        if (rec.unhandled.length) {
            // The fallback: compare every block the unhandled steps touched with its shadow.
            const r = this.reconcileAll(tr.doc, this.touched(tr, rec.unhandled.map(s => tr.steps.indexOf(s))));
            rec.ops.push(...r.ops);
            rec.cids.push(...r.cids);
        }
        this.queue(rec.ops, rec.cids);
    }

    /** Record what a finished composition left in each block it touched. */
    settle(doc: PMNode): void {
        if (!this.composed.size) return;
        const r = this.reconcileAll(doc, this.composed);
        this.composed.clear();
        if (r.ops.length) this.queue(r.ops, r.cids);
    }

    /** The blocks with shadows that the steps at `indices` changed, found in the transaction's final document. */
    private touched(tr: Transaction, indices: number[]): Set<string> {
        const out = new Set<string>();
        for (const i of indices) {
            const rest = tr.mapping.slice(i + 1);
            tr.steps[i].getMap().forEach((_a, _b, fromB, toB) => {
                const from = rest.map(fromB, -1), to = rest.map(toB, 1);
                tr.doc.nodesBetween(Math.max(0, from - 1), Math.min(tr.doc.content.size, to + 1), (n, _pos, parent) => {
                    if (parent === tr.doc && n.attrs.bid && this.shadows.has(n.attrs.bid)) out.add(n.attrs.bid);
                    return false;
                });
            });
        }
        return out;
    }

    private reconcileAll(doc: PMNode, bids: Iterable<string>): { ops: OpBody[]; cids: string[] } {
        const out = { ops: [] as OpBody[], cids: [] as string[] };
        for (const bid of bids) {
            let node: PMNode | null = null;
            doc.forEach(n => { if (n.attrs.bid === bid) node = n; });
            const shadow = this.shadows.get(bid);
            if (!node || !shadow) continue;
            const r = reconcile(node, shadow, newCid);
            out.ops.push(...r.ops);
            out.cids.push(...r.cids);
        }
        return out;
    }

    private queue(ops: OpBody[], cids: string[], holds: string[] = []): void {
        let ci = 0;
        let first = '';
        const hold = (bid: unknown, cid: string) => {
            if (typeof bid !== 'string' || !bid) return;
            this.unacked.set(bid, (this.unacked.get(bid) ?? new Set()).add(cid));
            this.onHoldChange(bid);
        };
        for (const op of ops) {
            // An insert keeps the client id its shadow refs were minted under.
            const cid = op.kind === 'text.insert' ? (cids[ci++] ?? newCid()) : newCid();
            first ||= cid;
            // Every block the op names, not only its target: a merge is about
            // the block that dies as much as the one that survives, and a split
            // about the one it mints.
            for (const bid of [op.target.bid, op.target.into, op.target.fid, op.payload?.new]) hold(bid, cid);
            this.pending.push({ cid, batch: this.batch, ...op });
        }
        for (const bid of holds) hold(bid, first || newCid());
        if (this.timer) window.clearTimeout(this.timer);
        this.timer = window.setTimeout(() => this.flush(), SETTLE_MS);
    }

    /** Send what is queued now, as one batch. */
    flush(): void {
        if (this.timer) { window.clearTimeout(this.timer); this.timer = undefined; }
        if (!this.pending.length) return;
        this.submit(this.pending);
        this.pending = [];
        this.batch = newBatch();
    }

    /**
     * Take the daemon's answers. An applied op turns the shadow's provisional
     * refs into real ones and becomes its blocks' `lastOp`. Returns the blocks
     * whose op was refused, with the reason. Such a block's shadow now says
     * something the store does not, so it is marked to be re-read from the
     * projection (`reloadable`), but only once nothing else of it is in flight:
     * re-reading it earlier would take away the words its other ops carry.
     */
    ack(acks: Ack[]): Map<string, string> {
        const refused = new Map<string, string>();
        for (const a of acks) {
            const waiting: string[] = [];
            for (const [bid, set] of this.unacked) {
                if (set.delete(a.cid)) {
                    waiting.push(bid);
                    this.onHoldChange(bid);
                    if (a.conflict) { refused.set(bid, a.conflict); this.stale.add(bid); }
                    if (set.size === 0) this.unacked.delete(bid);
                }
            }
            if (a.op && !a.conflict) {
                this.applied.add(a.op);
                for (const bid of waiting) {
                    this.shadows.get(bid)?.resolve(a.cid, a.op);
                    for (const sh of this.cards.get(bid)?.shadows.values() ?? []) sh.resolve(a.cid, a.op);
                    this.lastOp.set(bid, a.op);
                }
            }
        }
        return refused;
    }

    /** The blocks a projection with this frontier must leave alone. */
    held(frontier: Record<string, number>): Set<string> {
        const out = new Set<string>(this.composed);
        for (const bid of new Set([...this.unacked.keys(), ...this.lastOp.keys()])) {
            const op = this.lastOp.get(bid);
            if ((this.unacked.get(bid)?.size ?? 0) > 0 || (op !== undefined && !inFrontier(op, frontier))) out.add(bid);
        }
        return out;
    }

    /** The refused blocks a projection with this frontier may now replace:
     *  none of their ops in flight, and their last applied op in it. Each is
     *  returned once. */
    reloadable(frontier: Record<string, number>): Set<string> {
        const held = this.held(frontier);
        const out = new Set([...this.stale].filter(bid => !held.has(bid)));
        for (const bid of out) this.stale.delete(bid);
        return out;
    }

    /** A projection with this frontier was adopted: its runs now say who wrote the ops it covers. */
    covered(frontier: Record<string, number>): void {
        for (const op of this.applied) if (inFrontier(op, frontier)) this.applied.delete(op);
    }

    /** The daemon rebuilt its store (a commit, a checkout, a fold), so the ops
     *  it applied before say nothing about the new frontier: forget them. Ops
     *  in flight still hold their blocks; the new store answers them. */
    rebuilt(): void {
        for (const bid of this.lastOp.keys()) this.onHoldChange(bid);
        this.lastOp.clear();
        this.stale.clear();
    }

    /** A block has left the page for good. */
    forget(bid: string): void {
        this.lastOp.delete(bid);
    }
}

/** The blocks a gesture put on the page or took off it. They are held with the
 *  blocks its ops name, because a projection that predates the gesture still
 *  lists what it removed and still lacks what it added. */
function bornOrGone(tr: Transaction): string[] {
    const before = new Set<string>(), after = new Set<string>();
    tr.before.forEach(n => { if (n.attrs.bid) before.add(n.attrs.bid as string); });
    tr.doc.forEach(n => { if (n.attrs.bid) after.add(n.attrs.bid as string); });
    return [...before, ...after].filter(b => before.has(b) !== after.has(b));
}
