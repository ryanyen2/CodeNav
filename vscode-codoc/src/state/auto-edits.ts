/**
 * auto-edits.ts — which unasked rewrites the reader still owes attention to.
 *
 * Loop A applies safe ops without asking, and one of them changes what the document
 * SAYS: a small AMEND rewrites a description in place. Nobody is prompted, so unless
 * the author happens to reread that paragraph they never learn it moved. Everything
 * else the loop does automatically (refresh a fingerprint, attach or detach a chunk)
 * is machinery, and machinery that announces itself is noise wearing a badge — the
 * triage lives in `render._auto_edits`, which is why this module only ever sees
 * amends.
 *
 * The model here is deliberately NOT a log. A log is a place you have to remember to
 * visit, and nobody visits it. Instead each rewrite is UNSEEN until the reader has
 * actually been on that section, at which point it clears itself and never returns.
 * The acknowledgement is reading, not dismissing.
 *
 * WEIGHTING is the whole reason this stays calm. `is_small_amend` already holds the
 * two authorships to different bars — a person's own prose must survive ≥85% intact
 * to be auto-applied at all, while the loop may freely revise its own bootstrap text.
 * So the loop editing its own words is housekeeping and clears on a glance, whereas
 * the loop editing YOURS is a real event and is held to a longer dwell. Same channel,
 * two intensities; no second surface, no counter that only grows.
 *
 * Pure — the timing/DOM side lives in the webview.
 */
import type { AutoEdit } from './bindings-model';

/** How long a feature must be the reader's current section before its rewrite counts
 *  as seen. Loop-authored prose clears on a glance; a rewrite of the reader's OWN
 *  words waits until they have genuinely settled there, so it survives a fast scroll
 *  down the document. */
export const DWELL_LOOP_MS = 900;
export const DWELL_HUMAN_MS = 2200;

/** The rewrite displaced words a person wrote (rather than the loop's own prose). */
export function displacedHuman(e: AutoEdit): boolean {
    return e.written_by === 'human';
}

export function dwellFor(e: AutoEdit): number {
    return displacedHuman(e) ? DWELL_HUMAN_MS : DWELL_LOOP_MS;
}

/**
 * The seen-set is keyed by `fid@at`, not by `fid`.
 *
 * A feature can be rewritten again later, and that later rewrite is a new thing to
 * know about — keying on the feature alone would mark every future rewrite as already
 * seen the moment the first one was. `at` is the event's HLC, so a fresh rewrite
 * always produces a key nobody has acknowledged.
 */
export function editKey(fid: string, e: AutoEdit): string {
    return fid + '@' + e.at;
}

/** Rewrites the reader has not caught up on yet, in document order as given. */
export function unseenEdits(
    edits: Record<string, AutoEdit>, seen: ReadonlySet<string>, order: readonly string[],
): { fid: string; edit: AutoEdit }[] {
    const out: { fid: string; edit: AutoEdit }[] = [];
    for (const fid of order) {
        const edit = edits[fid];
        if (edit && !seen.has(editKey(fid, edit))) out.push({ fid, edit });
    }
    return out;
}

/**
 * Drop acknowledgements for rewrites that are no longer on offer.
 *
 * Without this the set grows for the life of the workspace and, worse, keeps keys for
 * features that were retired and could be re-created. Called on every payload with
 * the keys currently in play.
 */
export function pruneSeen(
    seen: ReadonlySet<string>, edits: Record<string, AutoEdit>,
): Set<string> {
    const live = new Set(Object.entries(edits).map(([fid, e]) => editKey(fid, e)));
    return new Set([...seen].filter(k => live.has(k)));
}

/** The catch-up line's wording. Says the number and, when any of them displaced the
 *  reader's own words, says that too — the one distinction worth spending words on. */
export function catchUpLabel(unseen: { edit: AutoEdit }[]): string {
    const n = unseen.length;
    if (!n) return '';
    const mine = unseen.filter(u => displacedHuman(u.edit)).length;
    const noun = n === 1 ? 'description' : 'descriptions';
    if (!mine) return `codoc rewrote ${n} ${noun}`;
    if (mine === n) return n === 1 ? 'codoc edited your wording' : `codoc edited your wording in ${n} places`;
    return `codoc rewrote ${n} ${noun} (${mine} of yours)`;
}
