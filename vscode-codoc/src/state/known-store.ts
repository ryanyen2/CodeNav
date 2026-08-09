/**
 * known-store.ts — what THIS host has written that the store has not echoed back yet.
 *
 * A command carries `base_text`: the value the AUTHOR last knew the store to hold for
 * the field it replaces (see `loop_b._resolve_content`). Getting that value right is
 * what makes the daemon's three-way merge work at all — a wrong base_text that happens
 * to equal the store's current text reads as "clean continuation" and applies verbatim,
 * silently erasing whoever wrote in between. That is the exact class the command ledger
 * exists to prevent, so the provenance of this one string has to be exact.
 *
 * There are only two honest sources for it:
 *
 *   1. the baseline the settle CITES — the projection the author was actually looking
 *      at when they typed (`commands-from-doc.settleCommands`), and
 *   2. this host's OWN emitted commands — text the author put there themselves, which
 *      the projection has not returned yet. Without this half, two settles in a row
 *      (ordinary typing, faster than the round trip) would each cite text the store had
 *      already moved past, and every one of them would read as a conflict.
 *
 * A projection is NOT a third source, and the distinction is the whole point of this
 * module. The host reads projections eagerly, and the webview may never adopt the one
 * it just read: the doc gate defers during IME composition and while a comment composer
 * is open, and it keeps a feature local when the author has an unsent edit in it. So a
 * fresh projection is "what the store holds", which is precisely NOT "what the author
 * last knew" whenever a third party wrote between the author's last adopt and their next
 * settle. Sourcing base_text from it claimed the author had seen a write they never saw.
 *
 * Hence: an OPTIMISTIC OVERLAY, per feature and PER FIELD. Entries are created only by
 * `advance` (this host's own successful appends) and removed only by `prune` (a projection
 * showing the store agrees, i.e. the command landed). What a projection can do is retire
 * an entry; what it can never do is invent one. Everything a settle does not find here
 * falls back to the cited baseline unit — what the author saw.
 *
 * Per FIELD, not per feature, because a title edit says nothing about what the author
 * last knew the description to be. A whole-unit overlay had to fill the other field from
 * somewhere, and the only thing available was the projection — smuggling the projection
 * back in through the field the author never touched.
 *
 * Pure + side-effect-free so vitest pins the contract; `tree-editor.ts` holds the map.
 */
import type { CommandEntry } from './edits-channel';
import type { FeatureUnit } from './commands-from-doc';

/** One feature's unacknowledged local writes. A field is present only when this host
 *  wrote it and no projection has confirmed it yet. */
export interface KnownText {
    title?: string;
    description?: string;
}

/** The overlay: fid → the fields this host wrote and is still waiting to see echoed. */
export type KnownStore = ReadonlyMap<string, KnownText>;

export function emptyKnownStore(): KnownStore {
    return new Map<string, KnownText>();
}

/**
 * Fold successfully-appended commands into the overlay, so the NEXT command cites the
 * text this one is about to write rather than the text before it.
 *
 * Only content kinds record anything. A `move` changes no text, and a `retire` ends the
 * feature — recording either would leave an entry no projection can ever confirm, which
 * would pin a stale base_text on the feature forever.
 *
 * Call this ONLY after the append succeeded: if the edit never reached the log, the
 * store never moved, and claiming it did would make the author's next edit cite text
 * that exists nowhere.
 */
export function advanceKnown(prev: KnownStore, commands: readonly CommandEntry[]): KnownStore {
    let out: Map<string, KnownText> | null = null;
    const edit = (fid: string): KnownText => {
        if (!out) out = new Map(prev);
        const next = { ...(out.get(fid) ?? {}) };
        out.set(fid, next);
        return next;
    };
    for (const c of commands) {
        const fid = c.feature_id;
        if (!fid) continue;
        if (c.kind === 'set_title' && typeof c.payload?.title === 'string') {
            edit(fid).title = c.payload.title;
        } else if (c.kind === 'set_description' && typeof c.payload?.description === 'string') {
            edit(fid).description = c.payload.description;
        }
    }
    return out ?? prev;
}

/**
 * Drop what a projection confirms — the only thing a projection is allowed to do here.
 *
 * A field whose recorded text now matches the projection has been absorbed by the store,
 * so the cited baseline is a better base_text than the overlay from here on (it tracks
 * what the author sees). A field that does NOT match is kept: either the command is still
 * in flight (the daemon has not merged `edits.host.jsonl` yet, and a Loop-A tick can
 * re-render the projection from pre-command text in the meantime) or somebody else has
 * written since. Both are cases where "what the author last knew" is still the overlay's
 * text, and keeping it is what lets the daemon see the divergence and merge instead of
 * overwriting.
 *
 * A feature absent from the projection (retired, or not yet minted) is dropped: nothing
 * can confirm it and nothing can use it, so keeping it would only leak.
 *
 * Comparison is exact, so an entry can also linger because the store round-tripped the
 * text through render+parse and normalized whitespace the author cannot see. That is
 * harmless rather than a stuck conflict: the daemon's own `moved` test normalizes both
 * sides, so a base that differs only in invisible whitespace still reads as clean. The
 * entry is replaced the next time its author edits that field.
 */
export function pruneKnown(prev: KnownStore, projection: readonly FeatureUnit[]): KnownStore {
    if (!prev.size) return prev;
    const byFid = new Map<string, FeatureUnit>();
    for (const u of projection) if (u.fid) byFid.set(u.fid, u);
    const out = new Map<string, KnownText>();
    for (const [fid, known] of prev) {
        const unit = byFid.get(fid);
        if (!unit) continue;             // gone from the store — nothing to confirm against
        const next: KnownText = {};
        if (known.title !== undefined && known.title !== unit.title) next.title = known.title;
        if (known.description !== undefined && known.description !== unit.description) {
            next.description = known.description;
        }
        if (next.title !== undefined || next.description !== undefined) out.set(fid, next);
    }
    return out;
}
