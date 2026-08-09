/**
 * edit-provenance.ts — the bookkeeping that lets an authored edit say, truthfully, what
 * state it was made against.
 *
 * A command carries two provenance facts, and both have exactly one correct source:
 *
 *   • the BASELINE it was computed from — the projection the author was looking at, named
 *     by the id the editor stamped when it adopted that projection; and
 *   • the `base_text` it replaces — this surface's own not-yet-echoed writes, else the
 *     text of that same cited baseline (see known-store.ts).
 *
 * Getting either from "the newest projection this process has read" is the silent
 * data-loss bug the command channel exists to prevent: a base that equals what the store
 * currently holds reads as a clean continuation, so `loop_b._resolve_content` applies the
 * incoming text verbatim over whoever wrote in between — no merge, no arbitration, nothing
 * in the event ledger.
 *
 * Both editing homes need this, and for the same reason they must not each have their own
 * copy of it: the VS Code extension host owns the projections it builds, and on the hub
 * the browser is the only party that ever sees one. Two implementations of a rule whose
 * failure mode is silence would drift without anything going red.
 *
 * Pure (no vscode, no DOM, no IO) so vitest pins the contract; the host keeps one of these
 * per open document and the hub keeps one per tab.
 */
import {
    settleCommands, type Baseline, type FeatureUnit,
} from './commands-from-doc';
import { advanceKnown, pruneKnown, emptyKnownStore, type KnownStore } from './known-store';
import type { CommandEntry } from './edits-channel';

/** How many projection baselines a settle may still cite.
 *
 *  A settle names the baseline its content was typed against, and that baseline has to
 *  still be here to be resolved. The window has to cover the projections that can land
 *  between two settles of the same surface — a daemon working through an agent's realize
 *  pass writes several per second — while staying bounded so a long editing session cannot
 *  grow it forever. Eviction is also made rarer by dropping everything older than each
 *  cited baseline, since a citation only ever moves forward. */
const BASELINE_HISTORY = 16;

export class EditProvenance {
    private history: Baseline[] = [];
    private fallback: FeatureUnit[] | null = null;
    private optimistic: KnownStore = emptyKnownStore();

    /** `session` names this editing session — a window, or a browser tab. It rides on every
     *  command so the daemon can tell a burst of this author's own edits (their base
     *  legitimately trails the projection round trip) from a real disagreement with
     *  somebody else. Distinct per surface, stable for that surface's life. */
    constructor(private readonly session: string) {}

    /**
     * Record a projection: it becomes a citable baseline, and it RETIRES the optimistic
     * entries it confirms.
     *
     * Retiring is the only thing a projection may do to the overlay — it may never seed
     * one. A projection is "what the store holds", which is precisely not "what the author
     * last knew" whenever a third party has written and this surface has not adopted the
     * result yet (the doc gate defers during IME composition and while a comment composer
     * is open, and keeps a feature local while its edit is unsent).
     *
     * Call this for every projection read, adopted or not: the editor decides what it
     * adopts, and this only has to be able to resolve whatever a settle later cites. A
     * projection with no id is still recorded as the fallback but cannot be cited.
     */
    observe(units: FeatureUnit[], baselineId?: number): void {
        this.fallback = units;
        this.optimistic = pruneKnown(this.optimistic, units);
        if (baselineId == null) return;
        this.history = [...this.history.filter(b => b.id !== baselineId), { id: baselineId, units }];
        if (this.history.length > BASELINE_HISTORY) this.history = this.history.slice(-BASELINE_HISTORY);
    }

    /**
     * The commands a settled document implies, diffed against the baseline it CITES.
     *
     * Does NOT advance the overlay — call {@link record} once the commands are safely on
     * their way. The two are separate because "sent" means different things in the two
     * homes (an append to the host op log; an enqueue onto the network outbox), and
     * claiming a write the channel dropped would make the author's next edit cite text
     * that exists nowhere.
     *
     * `unobservedFallback` is consulted only when no projection has been observed at all —
     * a surface that somehow settles before its first payload. The host reads
     * `tree.doc.json` from disk for it, which is why it is a thunk.
     */
    settle(
        next: FeatureUnit[],
        citedBaselineId: number | undefined,
        token: string,
        unobservedFallback?: () => FeatureUnit[],
    ): CommandEntry[] {
        const fallback = this.fallback ?? unobservedFallback?.() ?? [];
        const commands = settleCommands(this.history, citedBaselineId, fallback, next,
                                        token, this.optimistic, this.session);
        // A citation only moves forward (the editor stamps it on adopt), so baselines older
        // than this one are dead — dropping them keeps the live ones inside the window, so
        // a burst of daemon passes between two settles cannot push out the baseline the
        // next settle is about to cite.
        if (citedBaselineId != null) {
            this.history = this.history.filter(b => b.id >= citedBaselineId);
        }
        return commands;
    }

    /** Fold commands this surface has successfully sent into the overlay, so the NEXT
     *  command cites the text they wrote rather than the text before it. Covers both a
     *  settle's commands and an explicitly authored one (a drag). */
    record(commands: readonly CommandEntry[]): void {
        this.optimistic = advanceKnown(this.optimistic, commands);
    }
}
