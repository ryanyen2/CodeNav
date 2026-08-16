// What kind of change an edit to the description was.
//
// The plan wanted composition steps: how a piece of writing was typed, captured
// on pauses. That is only observable in one condition. In the baseline the
// description is an ordinary markdown file and every keystroke reaches the
// logger; in the codoc condition it is edited in a custom editor that reports the
// text before and after and nothing in between. A measure built on typing would
// therefore exist on one side only, which is the same trap as counting accepts
// against a condition that cannot accept.
//
// So what is classified here is the change rather than the typing. Both
// conditions produce a before and an after, so both produce the same labels, and
// the question "what kind of edits do people make to the description" can be
// asked of the study rather than of one arm of it.

/** The whole set. Small on purpose, so counts across participants mean something. */
export const EDIT_KINDS = Object.freeze([
    'added',        // new text, nothing removed
    'extended',     // mostly kept, meaningfully longer
    'trimmed',      // mostly kept, meaningfully shorter
    'reworded',     // similar length, different words
    'rewritten',    // little of the original survives
    'removed',      // text taken out, nothing put back
    'unchanged',
]);

const GROWTH = 0.15;   // length change that counts as more than a touch-up
const KEPT_LOW = 0.35; // little of the original surviving
const NOVEL_LOW = 0.25; // little of the result being new material

/**
 * How much of `before` survives in `after`, from 0 to 1.
 *
 * Word level rather than character level, because a rename that touches every
 * line still leaves the writing intact and should not read as a rewrite.
 */
export function keptRatio(before, after) {
    const a = words(before);
    const b = new Map();
    for (const w of words(after)) b.set(w, (b.get(w) || 0) + 1);
    if (!a.length) return 0;
    let kept = 0;
    for (const w of a) {
        const n = b.get(w) || 0;
        if (n > 0) { kept += 1; b.set(w, n - 1); }
    }
    return kept / a.length;
}

const words = (s) => String(s || '').toLowerCase().match(/[\p{L}\p{N}']+/gu) || [];

/**
 * How much of `after` is material that was not there before, from 0 to 1.
 *
 * Needed alongside keptRatio because on its own that number cannot tell a
 * deletion from a rewrite: both leave little of the original. Cutting a
 * paragraph in half brings in nothing new, replacing it brings in everything.
 */
export function novelRatio(before, after) {
    return keptRatio(after, before) === 0 && !words(after).length
        ? 0
        : 1 - keptRatio(after, before);
}

/** One label for an edit, plus the numbers behind it. */
export function classifyEdit(before, after) {
    const b = String(before || '');
    const a = String(after || '');
    const stats = {
        beforeChars: b.length,
        afterChars: a.length,
        beforeWords: words(b).length,
        afterWords: words(a).length,
        kept: 0,
    };

    if (b === a) return { kind: 'unchanged', ...stats, kept: 1 };
    if (!b.trim()) return { kind: 'added', ...stats, kept: 0 };
    if (!a.trim()) return { kind: 'removed', ...stats, kept: 0 };

    const kept = keptRatio(b, a);
    const novel = novelRatio(b, a);
    stats.kept = Math.round(kept * 100) / 100;
    stats.novel = Math.round(novel * 100) / 100;
    const growth = (stats.afterWords - stats.beforeWords) / Math.max(stats.beforeWords, 1);

    // Nothing new came in, so this is the same writing at a different length,
    // however much of it went. A paragraph cut in half is a cut, not a rewrite.
    if (novel <= NOVEL_LOW) {
        if (growth > GROWTH) return { kind: 'extended', ...stats };
        if (growth < -GROWTH) return { kind: 'trimmed', ...stats };
        return { kind: 'reworded', ...stats };
    }
    // New material came in and little of the original stayed.
    if (kept < KEPT_LOW) return { kind: 'rewritten', ...stats };
    if (growth > GROWTH) return { kind: 'extended', ...stats };
    if (growth < -GROWTH) return { kind: 'trimmed', ...stats };
    return { kind: 'reworded', ...stats };
}

/**
 * Whether an edit put a reason into the description rather than only a
 * restatement of what the code does.
 *
 * Deliberately shallow. It flags text worth a human reading, and is never the
 * measure itself, because the study scores rationale by hand against a written
 * key. Used to find the edits worth looking at first among a hundred.
 */
const REASON_HINTS = /\b(because|so that|rather than|instead of|we (chose|rejected|tried)|the reason|otherwise|which is why|trade-?off)\b/i;

export function mentionsReason(text) {
    return REASON_HINTS.test(String(text || ''));
}

/** Summarise a run of edits to one feature or file, for the dashboard. */
export function summarise(edits) {
    const counts = {};
    for (const e of edits || []) counts[e.kind] = (counts[e.kind] || 0) + 1;
    return {
        total: (edits || []).length,
        counts,
        netWords: (edits || []).reduce((n, e) => n + (e.afterWords - e.beforeWords), 0),
        withReason: (edits || []).filter((e) => e.reason).length,
    };
}
