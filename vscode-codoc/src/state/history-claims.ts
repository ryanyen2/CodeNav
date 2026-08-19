/**
 * history-claims.ts — the past, marked in the same grammar as the present.
 *
 * The timeline could already put Tuesday's words on screen. What it could not do was
 * say WHAT CHANGED at Tuesday's moment in the same terms the live document uses — it
 * had `changesAt`, which reports a before/after per FEATURE, and a whole-node
 * before/after is not a mark. A reader dragging the scrubber to a moment wants to see
 * the sentence that moved, in the channel of whoever moved it; being handed two
 * versions of a paragraph and asked to spot the difference is the job the surface
 * exists to do for them.
 *
 * So a moment's changes are projected through `claimsFor` — the same function the live
 * document is drawn from, at the same sub-sentence granularity, producing the same
 * `Claim` shape the same decoration layer renders. The past is not a second rendering
 * path with a second set of rules to keep in sync; it is the one model, asked about a
 * different moment.
 *
 * ## Which channel a past change is in
 *
 * The ledger already records it: `actor`. A moment the author made is theirs — drawn
 * in the human channel, and `committed`, because by the time it is history it has been
 * handed over. Everything else (the loop, an agent) reached the document by way of the
 * code, which is the code channel. The plan channel deliberately has no history: a
 * proposal that was never accepted was never applied, so it is in no snapshot, and one
 * that WAS accepted is in the ledger as an ordinary applied change.
 *
 * ## It still says what it cannot reconstruct
 *
 * A change whose displaced text the ledger never recorded yields NO claims — not an
 * empty diff, and not a diff against the current words. `changesAt` marks those
 * `unresolved` and this refuses them, because a mark drawn over invented text is worse
 * than no mark: the reader has no way to know which one they are looking at. The
 * surface reports the gap instead.
 *
 * Pure — no DOM, no TipTap.
 */
import { claimsFor, type Claim, type FeatureText } from './settlement';
import type { FeatureChange, SnapshotFeature } from './revision-model';

/** A snapshot feature as the settlement model reads text. Description paragraphs are
 *  split the way the renderer round-trips them. */
function textOf(f: SnapshotFeature | null): FeatureText {
    if (!f) return { title: '', paras: [] };
    return {
        title: f.title,
        paras: f.description ? f.description.split(/\n{2,}/) : [],
    };
}

/** Whether a moment's actor was the person reading. Anything the ledger does not call
 *  `human` reached the document by way of the code. */
export function isHumanActor(actor: string): boolean {
    return actor === 'human';
}

/**
 * One moment's changes, as claims on the document AS IT READ AFTER that moment.
 *
 * Keyed by feature so a caller can hand each feature its own claims; the offsets are
 * into that feature's post-moment text, which is exactly what the scrubber is showing.
 */
export function claimsForMoment(
    changes: readonly FeatureChange[], actor: string,
): Map<string, Claim[]> {
    const out = new Map<string, Claim[]>();
    const human = isHumanActor(actor);
    for (const ch of changes) {
        // No claims for a change the ledger cannot account for — see the header.
        if (ch.unresolved) continue;
        const before = textOf(ch.before);
        const after = textOf(ch.after);
        const claims = human
            // Already handed over by the time it is history, so `committed`: the past
            // has no unsent edits in it.
            ? claimsFor({ projected: after, live: after, humanBase: before, committed: true })
            : claimsFor({ code: { layerId: ch.fid, prev: before }, projected: after, live: after });
        if (claims.length) out.set(ch.fid, claims);
    }
    return out;
}

/**
 * Features the moment touched but could not account for.
 *
 * Returned separately rather than folded into the claims because they are a different
 * statement — "this changed and I cannot show you how" is not a kind of diff, and a
 * surface that renders it as one has stopped being trustworthy about the rest.
 */
export function unaccountedAt(changes: readonly FeatureChange[]): Set<string> {
    return new Set(changes.filter(c => c.unresolved).map(c => c.fid));
}
