// Estimates and intervals, for figures that report a difference.
//
// Two decisions run through this file.
//
// The design is within subjects, so every difference is PAIRED: one number per
// participant, codoc minus baseline, and the interval is around the mean of
// those. Treating the two conditions as independent samples would throw away the
// pairing the design was built to get, widen every interval, and answer a
// question nobody asked.
//
// The resampling is seeded. An unseeded bootstrap gives a slightly different
// interval every time the figure is drawn, so the number in the caption stops
// matching the number in the picture the moment either is redrawn, and nobody
// can reproduce the figure from the data. The seed is part of the method.

/** A small deterministic generator, so a figure redraws identically. */
export function rng(seed = 20260816) {
    let s = seed >>> 0;
    return () => {
        // xorshift32: short, adequate for resampling, and the same everywhere.
        s ^= s << 13; s >>>= 0;
        s ^= s >>> 17;
        s ^= s << 5; s >>>= 0;
        return s / 4294967296;
    };
}

export const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);

export function sd(xs) {
    if (xs.length < 2) return 0;
    const m = mean(xs);
    return Math.sqrt(xs.reduce((a, x) => a + (x - m) ** 2, 0) / (xs.length - 1));
}

const se = (xs) => (xs.length ? sd(xs) / Math.sqrt(xs.length) : 0);

/**
 * One difference per participant who did both conditions.
 *
 * Anyone missing either side is dropped rather than filled in. A participant who
 * only did one condition has no difference, and inventing one from the group
 * mean would shrink the interval using a person who never provided the number.
 */
export function pairedDiffs(rows, { a = 'codoc', b = 'baseline', key = 'value' } = {}) {
    const by = new Map();
    for (const r of rows) {
        if (r[key] == null) continue;
        const entry = by.get(r.code) || {};
        entry[r.condition] = r[key];
        by.set(r.code, entry);
    }
    const out = [];
    for (const [code, v] of by) {
        if (v[a] == null || v[b] == null) continue;
        out.push({ code, diff: v[a] - v[b] });
    }
    return out;
}

/**
 * Studentized bootstrap interval for the mean of `xs`.
 *
 * Studentized rather than percentile because these are small samples of bounded
 * ordinal ratings, where the percentile interval is known to sit off-centre. It
 * costs an inner standard error per resample and nothing else.
 *
 * Returns null below four observations. An interval from three numbers is a
 * decoration, and drawing one invites a reader to take it seriously.
 */
export function studentizedCI(xs, { level = 0.95, resamples = 4000, seed } = {}) {
    const n = xs.length;
    if (n < 4) return null;
    const m = mean(xs);
    const s = se(xs);
    if (s === 0) return { mean: m, low: m, high: m, n, degenerate: true };

    const rand = rng(seed);
    const ts = [];
    const sample = new Array(n);
    for (let b = 0; b < resamples; b += 1) {
        for (let i = 0; i < n; i += 1) sample[i] = xs[Math.floor(rand() * n)];
        const sm = mean(sample);
        const ss = se(sample);
        // A resample with no spread gives no t. Skipping it is honest; using a
        // huge number in its place would blow the interval open on ties, which
        // are common in ordinal data.
        if (ss > 0) ts.push((sm - m) / ss);
    }
    if (ts.length < resamples / 10) return { mean: m, low: m, high: m, n, degenerate: true };
    ts.sort((x, y) => x - y);
    const q = (p) => ts[Math.min(ts.length - 1, Math.max(0, Math.floor(p * ts.length)))];
    const alpha = 1 - level;
    // Note the crossing: the upper t bound gives the LOWER end of the interval.
    return { mean: m, low: m - q(1 - alpha / 2) * s, high: m - q(alpha / 2) * s, n };
}

/** The paired difference and its interval, in one call. */
export function pairedEstimate(rows, opts = {}) {
    const diffs = pairedDiffs(rows, opts).map((d) => d.diff);
    const ci = studentizedCI(diffs, opts);
    return { n: diffs.length, diffs, ...(ci || { mean: mean(diffs), low: null, high: null }) };
}
