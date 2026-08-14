// Counting what recurs, and separating it from what is merely common.
//
// The trap this exists to avoid: the most frequent pair in any session will be
// whichever two actions are individually most frequent. Reading code follows
// reading code because people read a lot of code, not because there is a pattern
// there. Raw counts answer "what happens often", which nobody asked.
//
// So every pair is also scored against what its parts would predict if order did
// not matter. A pair that appears far more often than that is a habit; a pair
// that appears about as often is arithmetic.

/** Counts of every run of `n` actions inside each episode. */
export function count(episodes, n = 2) {
    const grams = new Map();
    let total = 0;
    for (const seq of episodes) {
        for (let i = 0; i + n <= seq.length; i += 1) {
            const key = seq.slice(i, i + n).join(' ');
            grams.set(key, (grams.get(key) || 0) + 1);
            total += 1;
        }
    }
    return { grams, total };
}

/** Counts of single actions, which the expected values are built from. */
export function unigrams(episodes) {
    const counts = new Map();
    let total = 0;
    for (const seq of episodes) {
        for (const a of seq) { counts.set(a, (counts.get(a) || 0) + 1); total += 1; }
    }
    return { counts, total };
}

/**
 * Score every pair or triple against what independence would predict.
 *
 * The score is log2 of observed over expected. Zero means the pair happens
 * exactly as often as its parts predict, so there is nothing there. Positive
 * means it recurs. It is reported alongside the count and never instead of it,
 * because the measure rewards rare events and a high score on three occurrences
 * is not a finding.
 *
 * `minCount` drops the tail. How much was dropped is returned rather than
 * quietly discarded: silently trimming is how a pattern gets invented.
 */
export function score(episodes, { n = 2, minCount = 3 } = {}) {
    const { grams, total } = count(episodes, n);
    const { counts: uni, total: uniTotal } = unigrams(episodes);
    if (!total) return { rows: [], total: 0, trimmed: 0, trimmedShare: 0 };

    const rows = [];
    let trimmed = 0;
    let trimmedCount = 0;
    for (const [key, k] of grams) {
        if (k < minCount) { trimmed += 1; trimmedCount += k; continue; }
        const parts = key.split(' ');
        const expected = parts.reduce((p, a) => p * ((uni.get(a) || 0) / uniTotal), 1) * total;
        rows.push({
            gram: key,
            parts,
            count: k,
            share: k / total,
            expected,
            lift: expected > 0 ? Math.log2(k / expected) : 0,
        });
    }
    rows.sort((a, b) => b.lift - a.lift || b.count - a.count);
    return {
        rows,
        total,
        // Both numbers, because "we dropped 400 rare pairs" and "we dropped 2% of
        // the data" are different facts and a reader needs the second one.
        trimmed,
        trimmedShare: trimmedCount / total,
        minCount,
    };
}

/**
 * The same, but every session counts once.
 *
 * Pooling raw counts lets one long session speak for the group: somebody who
 * worked twice as fast contributes twice the pairs. Here each session's own
 * shares are averaged, so the answer is what a typical session looks like rather
 * than what the busiest one did. `sessions` is a list of episode lists.
 */
export function scoreBySession(sessions, { n = 2, minCount = 3, minSessions = 2 } = {}) {
    const perSession = sessions.map((episodes) => count(episodes, n));
    const seen = new Map();     // gram -> { shares: [], sessions: number, count: number }

    perSession.forEach(({ grams, total }) => {
        if (!total) return;
        for (const [key, k] of grams) {
            const row = seen.get(key) || { shares: [], sessions: 0, count: 0 };
            row.shares.push(k / total);
            row.sessions += 1;
            row.count += k;
            seen.set(key, row);
        }
    });

    const pooled = score(sessions.flat(), { n, minCount });
    const lift = new Map(pooled.rows.map((r) => [r.gram, r.lift]));

    const rows = [];
    let trimmed = 0;
    for (const [gram, row] of seen) {
        // Something that happened in one session out of twelve is that person's
        // habit, not the group's.
        if (row.sessions < minSessions || row.count < minCount) { trimmed += 1; continue; }
        const mean = row.shares.reduce((a, b) => a + b, 0) / sessions.length;
        rows.push({
            gram,
            parts: gram.split(' '),
            count: row.count,
            sessions: row.sessions,
            meanShare: mean,
            lift: lift.get(gram) ?? 0,
        });
    }
    rows.sort((a, b) => b.meanShare - a.meanShare);
    return { rows, trimmed, sessions: sessions.length, minCount, minSessions };
}

/**
 * How the two conditions differ, described rather than tested.
 *
 * At a dozen participants this is a description of what was seen, and calling a
 * difference significant would be dressing up a sample too small to support it.
 * The pre-registration says which tests get run; this is for looking.
 */
export function compare(sessionsA, sessionsB, opts = {}) {
    const a = scoreBySession(sessionsA, opts);
    const b = scoreBySession(sessionsB, opts);
    const byGram = new Map();
    for (const r of a.rows) byGram.set(r.gram, { gram: r.gram, a: r.meanShare, b: 0, aSessions: r.sessions, bSessions: 0 });
    for (const r of b.rows) {
        const row = byGram.get(r.gram) || { gram: r.gram, a: 0, b: 0, aSessions: 0, bSessions: 0 };
        row.b = r.meanShare;
        row.bSessions = r.sessions;
        byGram.set(r.gram, row);
    }
    const rows = [...byGram.values()].map((r) => ({ ...r, diff: r.a - r.b }));
    rows.sort((x, y) => Math.abs(y.diff) - Math.abs(x.diff));
    return { rows, a, b };
}

/** A pair or triple written the way it is read aloud. */
export function label(gram) {
    return gram.split(' ').map((a) => a.toLowerCase().replace(/_/g, ' ')).join(' → ');
}
