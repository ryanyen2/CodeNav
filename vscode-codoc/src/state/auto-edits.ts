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
 * visit, and nobody visits it. Each rewrite is UNSEEN until the reader RESOLVES it
 * with an explicit Keep/Restore verdict on the in-situ diff (v7 — this replaced the
 * v6 dwell-to-clear model, whose marks evaporated the moment the reader looked at
 * them: a record of an AI edit that cannot be disagreed with is not a review
 * surface). Once resolved it never returns.
 *
 * WEIGHTING still matters: `is_small_amend` holds the two authorships to different
 * bars — a person's own prose must survive ≥85% intact to be auto-applied at all,
 * while the loop may freely revise its own bootstrap text. So the loop editing YOURS
 * is named as such ("codoc edited your wording") and drawn heavier; the housekeeping
 * case stays visually quiet. Same channel, two intensities.
 *
 * Pure — the DOM side lives in the webview (tiptap/auto-edit-decorations.ts).
 */
import type { AutoEdit } from './bindings-model';

/** The rewrite displaced words a person wrote (rather than the loop's own prose). */
export function displacedHuman(e: AutoEdit): boolean {
    return e.written_by === 'human';
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

/**
 * The Keep-all button's wording, and its warning.
 *
 * One verdict at a time is right when the loop rewrote a description here and
 * there. It is wrong after `codoc translate`, which rewrites every node: the
 * reader is asked for twenty-five verdicts on a rewrite they requested by name,
 * and the honest answer to each is the same one.
 *
 * Keep is the verdict that changes nothing — it records that the rewrite was
 * seen and leaves the prose alone — so doing it in bulk cannot lose work. Restore
 * is the one that writes, and it stays per-node deliberately: reverting
 * twenty-five descriptions in one click is a different and much worse button.
 *
 * The one thing bulk can hide is a rewrite that displaced the reader's OWN
 * wording, so that count goes on the button itself, the way Accept-all names the
 * proposals that ask the agent to write code.
 */
export function keepAllLabel(unseen: { edit: AutoEdit }[]): string {
    const n = unseen.length;
    if (!n) return '';
    const mine = unseen.filter(u => displacedHuman(u.edit)).length;
    return mine
        ? `✓ Keep all (${n}, ${mine} of your wording)`
        : `✓ Keep all (${n})`;
}

/** Every verdict Keep-all sends: one per unseen rewrite, all of them `keep`. */
export function keepAllVerdicts(
    unseen: { fid: string; edit: AutoEdit }[],
): { fid: string; at: string; keep: true; prev: string }[] {
    return unseen.map(({ fid, edit }) =>
        ({ fid, at: edit.at, keep: true as const, prev: edit.prev }));
}
