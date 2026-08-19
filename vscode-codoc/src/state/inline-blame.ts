/**
 * inline-blame.ts — who wrote THIS sentence (W9).
 *
 * ## Why the existing blame is the wrong granularity
 *
 * The History stance answers "who last changed this feature", once, on the heading. That
 * is not the question anyone asks. A feature is a paragraph or five, written over weeks
 * by a person, a loop pass and an agent in turn; "claude-code edited this 3h ago" says
 * nothing about which of its claims the agent wrote, and the claim is what the reader is
 * deciding whether to trust. git blame is per line. Google Docs colours per run. codoc
 * blamed per node, which is why it read as decoration rather than information.
 *
 * ## Where the finer answer comes from — no new transport
 *
 * The revisions window (`.codoc/revisions.json`) already carries, per applied event, the
 * text it wrote AND the text it displaced. Diffing those two, oldest first, says exactly
 * which words each event introduced; carrying the attribution forward through later
 * diffs says who owns each word that SURVIVED into the text on screen. That is real
 * per-span authorship, computed locally from data already on the wire.
 *
 * ## What it deliberately does not do
 *
 * It attributes only what the ledger recorded. A span it cannot trace stays unattributed
 * rather than being credited to the feature's last editor — the whole failure of the
 * per-node version was crediting a paragraph to whoever touched the node last, and doing
 * that per word would multiply the error rather than fix it.
 *
 * Pure: no DOM, no vscode, no TipTap.
 */
import { wordDiff } from './doc-diff';
import type { RevisionEntry, Timeline } from './revision-model';

/** A run of text and the party that introduced it. Offsets index the CURRENT text. */
export interface BlameSpan {
    from: number;
    to: number;
    /** `human`, `loop`, or an agent id (`claude-code`, `codex`, …). */
    actor: string;
    /** HLC of the event that introduced it — for "when", and to break ties. */
    at: string;
}

/** Attribution carried per character while replaying. `null` = never traced. */
type Owner = { actor: string; at: string } | null;

/**
 * Replay one feature's recorded description changes and return who owns each surviving
 * span of `current`.
 *
 * Walks OLDEST first: each event's `prev_description → description` diff says which words
 * that event added, and the words it kept inherit whoever owned them before. Ends by
 * aligning the last recorded text against `current` so that text the ledger never saw
 * (an edit still in the buffer, or older than the window) is simply left unowned.
 */
export function blameDescription(
    current: string, timeline: Timeline, fid: string,
): BlameSpan[] {
    const events = describingEvents(timeline, fid);
    if (!events.length || !current) return [];

    // Seed from the oldest recorded "before" text we have. Its words predate the window,
    // so nobody in it can be credited with them.
    const first = events[0];
    let text = first.prev_description ?? '';
    let owners: Owner[] = new Array(text.length).fill(null);

    for (const e of events) {
        if (e.description === undefined) continue;
        const before = e.prev_description ?? text;
        // A recorded `prev` that disagrees with what we replayed means the chain is
        // broken (a gap in the window, an unrecorded write). Trust the RECORD over the
        // replay and drop the attribution we cannot justify, rather than sliding every
        // later offset by the difference.
        if (before !== text) owners = new Array(before.length).fill(null);
        ({ text, owners } = carry(before, e.description, owners,
                                  { actor: e.actor, at: e.at }));
    }

    // Whatever the author has typed since the last recorded event is theirs to keep
    // unattributed — the ledger has not seen it, so nothing here can honestly claim it.
    if (text !== current) ({ owners } = carry(text, current, owners, null));

    return coalesce(owners.slice(0, current.length));
}

/** The events that actually rewrote this feature's prose, oldest first. */
function describingEvents(timeline: Timeline, fid: string): RevisionEntry[] {
    const out: RevisionEntry[] = [];
    for (const moment of timeline.moments) {          // oldest-first
        // `entries` are newest-first inside a moment, so read them backwards to keep the
        // whole sequence in chronological order.
        for (let i = moment.entries.length - 1; i >= 0; i--) {
            const e = moment.entries[i];
            if (e.feature_id === fid && e.description !== undefined) out.push(e);
        }
    }
    return out;
}

/** Diff `before → after`, carrying `owners` across kept text and stamping `owner` on
 *  inserted text (`null` owner leaves the insertion unattributed). */
function carry(
    before: string, after: string, owners: Owner[], owner: Owner,
): { text: string; owners: Owner[] } {
    const next: Owner[] = [];
    let cursor = 0;
    for (const run of wordDiff(before, after)) {
        if (run.t === 'same') {
            for (let i = 0; i < run.s.length; i++) next.push(owners[cursor + i] ?? null);
            cursor += run.s.length;
        } else if (run.t === 'ins') {
            for (let i = 0; i < run.s.length; i++) next.push(owner);
        } else {
            cursor += run.s.length;                   // deleted — its attribution goes too
        }
    }
    return { text: after, owners: next };
}

/** Adjacent characters with the same owner become one span; unowned runs are dropped. */
function coalesce(owners: Owner[]): BlameSpan[] {
    const out: BlameSpan[] = [];
    let start = -1;
    for (let i = 0; i <= owners.length; i++) {
        const o = owners[i] ?? null;
        const prev = start >= 0 ? owners[start] : null;
        const same = start >= 0 && o && prev && o.actor === prev.actor && o.at === prev.at;
        if (same) continue;
        if (start >= 0 && prev) out.push({ from: start, to: i, actor: prev.actor, at: prev.at });
        start = o ? i : -1;
    }
    return out;
}

/**
 * Trim a blame map to the spans worth drawing.
 *
 * Two rules, both about not turning prose into a quilt. A span shorter than a word is
 * noise — it is usually one party's punctuation inside another's sentence, and marking it
 * says nothing a reader can use. And when a whole description has one owner there is
 * nothing to distinguish, so the heading's own label already carries it: drawing an
 * underline beneath every word to say "all of this is by the same person" is the
 * node-level signal we removed, re-drawn per character.
 */
export const BLAME_MIN_SPAN = 12;

export function significantSpans(spans: BlameSpan[], textLength: number): BlameSpan[] {
    const kept = spans.filter(s => s.to - s.from >= BLAME_MIN_SPAN);
    if (kept.length <= 1) {
        const covers = kept.length === 1 && kept[0].to - kept[0].from >= textLength * 0.9;
        return covers ? [] : kept;
    }
    return kept;
}
