// A session as a sequence, and a sequence as episodes.
//
// Splitting on long gaps matters more than it sounds. Without it, a break for
// coffee joins whatever came before it to whatever came after, and that join is
// counted as a transition somebody made. Enough of those and the most common
// "pattern" in the study is an artifact of lunch.
import { sharedOnly, isShared } from '../shared/actions.js';

export const DEFAULTS = Object.freeze({
    episodeGapMs: 120_000,  // two minutes away and the thread was dropped
    minEpisode: 3,          // shorter than this says nothing about order
});

/**
 * Split a session into episodes: stretches of work with no long gap inside.
 *
 * The IDLE marks the vocabulary already inserts are the split points, plus any
 * raw gap that exceeds the threshold. Episodes shorter than `minEpisode` are
 * dropped and counted, because a two action episode contributes one transition
 * and mostly noise.
 */
export function toEpisodes(actions, opts = {}) {
    const { episodeGapMs, minEpisode } = { ...DEFAULTS, ...opts };
    const episodes = [];
    let current = [];
    let dropped = 0;

    const close = () => {
        if (current.length >= minEpisode) episodes.push(current);
        else if (current.length) dropped += current.length;
        current = [];
    };

    let previousEnd = null;
    for (const a of actions || []) {
        if (a.a === 'IDLE') { close(); previousEnd = null; continue; }
        if (previousEnd !== null && a.t - previousEnd >= episodeGapMs) close();
        current.push(a);
        previousEnd = a.t + (a.spanMs || a.ms || 0);
    }
    close();

    return { episodes, droppedActions: dropped };
}

/**
 * Everything a comparison between the two conditions is allowed to see.
 *
 * The codoc-only actions are removed first. Counting a verdict against a
 * condition that has no verdicts to give would report a difference belonging to
 * the tool rather than to the person, which is the one mistake this whole design
 * is arranged to prevent.
 */
export function comparableEpisodes(actions, opts = {}) {
    return toEpisodes(sharedOnly(actions), opts);
}

/** The letters of an episode, which is what patterns are counted over. */
export const letters = (episode) => episode.map((a) => a.a);

/**
 * Collapse a run of the same action into one.
 *
 * Optional and off by default. Reading three files in a row is three READ_CODEs,
 * and whether that is one act of orientation or three acts of navigation is a
 * question about the participant, not about the data. Turning this on answers a
 * different question; it does not clean the data.
 */
export function collapseRuns(seq) {
    return seq.filter((a, i) => i === 0 || a !== seq[i - 1]);
}

/**
 * Describe a set of sessions, so a reader can see what the counts rest on before
 * reading the counts.
 */
export function describe(sessions) {
    const sizes = sessions.map((s) => s.actions.length);
    const total = sizes.reduce((a, b) => a + b, 0);
    const sorted = [...sizes].sort((a, b) => a - b);
    const median = sorted.length
        ? sorted[Math.floor(sorted.length / 2)] : 0;
    return {
        sessions: sessions.length,
        actions: total,
        median,
        smallest: sorted[0] ?? 0,
        largest: sorted[sorted.length - 1] ?? 0,
        // A session many times the median will dominate any pooled count, which
        // is the reason the pattern counts are averaged per session as well.
        lopsided: sorted.length > 1 && sorted[sorted.length - 1] > 4 * Math.max(median, 1),
        allShared: sessions.every((s) => s.actions.every((a) => isShared(a.a))),
    };
}
