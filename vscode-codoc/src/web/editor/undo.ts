/**
 * undo.ts: what ⌘Z and ⇧⌘Z need to know (docs/editor.md, "Undo").
 *
 * ProseMirror's history is not installed. An undo submits the recorded
 * inverses of the newest batch this page sent that is not undone, chosen from
 * the daemon's history window by `nextUndo` (calc/window.ts). This module keeps
 * what that choice rests on: the batches the page sent, which of them the
 * daemon has not answered, and the undos it refused. The page sends what
 * `onHistory` returns and shows the notices.
 */
import { newCid } from '../calc/ids';
import type { Ack, Submission } from '../calc/projection';
import { nextUndo, type HistoryWindow } from '../calc/window';

const SENT_KEPT = 500;
/** Ops that can take away the block between a split's two halves. */
const REORDER = new Set(['block.delete', 'block.move', 'block.merge']);
/** Refusals no later change answers: the undo can never apply. */
const FINAL = new Set(['authority', 'verdict_folded', 'not_current_verdict', 'plan_not_open']);
/** Why an undo was refused, in the person's words; any other code is shown as it is. */
const WHY_NOT: Record<string, string> = {
    verdict_folded: 'it is in a snapshot now',
    not_current_verdict: 'the plan was decided again since',
    plan_open: 'the plan is open again',
    plan_not_open: 'the plan was decided since',
    authority: 'your role does not allow it',
};

/** The blocks an entry changed: its text snapshots and the blocks its inverse names. */
const touchedBy = (e: HistoryWindow['ops'][number]): string[] => [
    ...Object.keys(e.blocks),
    ...e.inverse.flatMap(o => [o.target.bid, o.target.into, ...((o.payload as { ids?: unknown }).ids as string[] ?? [])]),
].filter((x): x is string => typeof x === 'string');

/** An undo sent and not yet answered: its batch, what it undoes, the newest history
 *  position when it was sent, and the blocks the undone batch touched. */
type Undoing = { batch: string; what: string; pos: number; blocks: Set<string> };

export class Undo {
    /** The batches this page sent, oldest first. Only these are undone: never a
     *  teammate's batch merged through git, nor the person's own `codoc accept`
     *  in a terminal. Kept in the page's state, so a reload of the page still
     *  knows them; a page opened fresh has nothing to undo. */
    private readonly sent: Set<string>;
    /** Batch → the client ids it submitted that the daemon has not answered. An
     *  undo waits for these, because it must choose from a window that already
     *  carries the person's newest work. A batch leaves this map by its acks and
     *  not by appearing in the window: the window omits refused ops and holds
     *  only the newest 200, so a batch can be answered and never show up there. */
    private readonly inFlight = new Map<string, Set<string>>();
    /** Client id → the undo it belongs to, until the daemon answers it. */
    private readonly undoing = new Map<string, Undoing>();
    /** Undo batches the daemon refused. The next undo moves past them. One is
     *  offered again only when it may now succeed: its refusal was not final, and
     *  someone else has since changed a block it touches, or, for a split undo
     *  refused because a block sits between (`not_adjacent`), removed or moved a
     *  block. Typing or adding blocks elsewhere does not count, so an agent
     *  writing in another feature cannot make every ⌘Z resend it. */
    private readonly refused = new Map<string, Undoing & { conflict: string }>();
    /** An undo or a redo the person asked for, waiting for a history window. */
    private asked: 'undo' | 'redo' | null = null;

    constructor(private readonly state: { getState?(): unknown; setState?(s: unknown): void }) {
        const restored = state.getState?.() as { sent?: unknown } | undefined;
        this.sent = new Set(Array.isArray(restored?.sent) ? restored.sent : []);
    }

    /** Whether an undo or a redo waits for a history window. */
    get waiting(): boolean {
        return this.asked !== null;
    }

    /** ⌘Z (`redo` false) or ⇧⌘Z: the next history window answers it. */
    ask(redo: boolean): void {
        this.asked = redo ? 'redo' : 'undo';
    }

    /** Submissions the page is sending. */
    track(subs: Submission[]): void {
        for (const s of subs) {
            const cids = this.inFlight.get(s.batch) ?? new Set<string>();
            cids.add(s.cid);
            this.inFlight.set(s.batch, cids);
            this.sent.add(s.batch);
        }
        this.state.setState?.({ sent: [...this.sent].slice(-SENT_KEPT) });
    }

    /** The daemon's answers. Returns the notice for an undo it refused, if any. */
    onAcks(acks: Ack[]): string | null {
        let notice: string | null = null;
        for (const a of acks) {
            for (const [b, cids] of this.inFlight) {
                if (cids.delete(a.cid) && !cids.size) this.inFlight.delete(b);
            }
            const u = this.undoing.get(a.cid);
            if (!u) continue;
            this.undoing.delete(a.cid);
            if (a.conflict && !this.refused.has(u.batch)) {
                this.refused.set(u.batch, { ...u, conflict: a.conflict });
                notice = `Could not ${u.what}: ${WHY_NOT[a.conflict] ?? a.conflict}.`;
            }
        }
        return notice;
    }

    /** A history window arrived. Returns the undo's submissions, a notice that
     *  there is nothing to undo, or null when no undo was asked for or something
     *  this page sent is still unanswered (its ack asks for a window again).
     *  `viewer` is the page's actor (`status.actor`): only their ops are undone. */
    onHistory(w: HistoryWindow, viewer: string): Submission[] | string | null {
        if (!this.asked) return null;
        for (const e of w.ops) this.inFlight.delete(e.batch);
        if (this.inFlight.size) return null;
        const redo = this.asked === 'redo';
        this.asked = null;
        for (const [b, r] of this.refused) {
            const changed = w.ops.some(e => !this.sent.has(e.batch) && e.pos > r.pos
                && (touchedBy(e).some(x => r.blocks.has(x)) || (r.conflict === 'not_adjacent' && REORDER.has(e.kind))));
            if (!FINAL.has(r.conflict) && changed) this.refused.delete(b);
        }
        const next = nextUndo(w, this.sent, viewer, redo, new Set(this.refused.keys()));
        if (!next) return redo ? 'nothing to redo' : 'nothing to undo';
        const subs = next.ops.map(op => ({ cid: newCid(), batch: next.batch, ...op }));
        // What the person did in the original batch, by its op kinds, for the notice if the daemon refuses.
        const original = next.batch.replace(/^(u-)+/, '');
        const entries = w.ops.filter(e => e.batch === original || e.batch === next.batch.slice(2));
        const kinds = new Set(entries.filter(e => e.batch === original).map(e => e.kind));
        const did = kinds.has('plan.accept') ? 'accepting the plan' : kinds.has('plan.reject') ? 'rejecting the plan'
            : kinds.has('feature.handoff') ? 'the hand-off' : 'the edit';
        const undoing = { batch: next.batch, what: `${redo ? 'redo' : 'undo'} ${did}`, pos: Math.max(0, ...w.ops.map(e => e.pos)),
                          blocks: new Set(entries.flatMap(touchedBy)) };
        for (const s of subs) this.undoing.set(s.cid, undoing);
        return subs;
    }
}
