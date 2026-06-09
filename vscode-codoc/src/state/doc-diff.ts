/**
 * doc-diff.ts — word-level diff between two strings (pure, testable).
 *
 * The chosen diff model is "word-level look, block-level store": a suggestion
 * stores the settled `old` text and the proposed `new` text; the editor renders
 * the change as a word-level inline diff (struck deletions, highlighted
 * insertions). This is the diff engine for that rendering, extracted from the
 * former DOM-bound `renderInlineDiff` so it can be unit-tested and shared.
 */
export type DiffOp = 'same' | 'del' | 'ins';

export interface DiffRun {
    t: DiffOp;
    s: string;
}

/**
 * Token-level LCS diff, split on whitespace runs (whitespace preserved as its own
 * tokens so spacing round-trips). Adjacent runs of the same op are merged so the
 * result is a clean sequence of same/del/ins spans.
 */
export function wordDiff(oldStr: string, newStr: string): DiffRun[] {
    const a = String(oldStr).split(/(\s+)/);
    const b = String(newStr).split(/(\s+)/);
    const n = a.length;
    const m = b.length;

    // LCS length table (suffix DP), used to backtrack the alignment.
    const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
    for (let i = n - 1; i >= 0; i--) {
        for (let j = m - 1; j >= 0; j--) {
            dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
        }
    }

    const raw: DiffRun[] = [];
    const push = (t: DiffOp, s: string): void => { if (s !== '') raw.push({ t, s }); };
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
        if (a[i] === b[j]) { push('same', a[i]); i++; j++; }
        else if (dp[i + 1][j] >= dp[i][j + 1]) { push('del', a[i]); i++; }
        else { push('ins', b[j]); j++; }
    }
    while (i < n) push('del', a[i++]);
    while (j < m) push('ins', b[j++]);

    // Merge adjacent same-op runs.
    const out: DiffRun[] = [];
    for (const run of raw) {
        const last = out[out.length - 1];
        if (last && last.t === run.t) last.s += run.s;
        else out.push({ ...run });
    }
    return out;
}

/** True when the two strings differ (cheap guard before computing a full diff). */
export function changed(oldStr: string, newStr: string): boolean {
    return String(oldStr) !== String(newStr);
}

/**
 * Collapse long unchanged ("same") runs to a little context + "…", so a rendered diff
 * shows just the CHANGE rather than restating the whole (already-visible) text. Pure
 * display-shaping: del/ins runs pass through untouched; only leading/trailing/middle
 * "same" runs longer than the kept context are trimmed.
 */
export function compactRuns(runs: DiffRun[]): DiffRun[] {
    const KEEP = 6; // tokens (≈3 words) of context to keep beside a change
    const out: DiffRun[] = [];
    runs.forEach((run, idx) => {
        if (run.t !== 'same') { out.push(run); return; }
        const toks = run.s.split(/(\s+)/).filter(t => t.length > 0);
        const first = idx === 0;
        const last = idx === runs.length - 1;
        if (first && last) { out.push(run); return; } // wholly unchanged — leave as-is
        if (first) {
            out.push(toks.length > KEEP ? { t: 'same', s: '… ' + toks.slice(-KEEP).join('') } : run);
        } else if (last) {
            out.push(toks.length > KEEP ? { t: 'same', s: toks.slice(0, KEEP).join('') + ' …' } : run);
        } else {
            out.push(toks.length > KEEP * 2 + 1
                ? { t: 'same', s: toks.slice(0, KEEP).join('') + ' … ' + toks.slice(-KEEP).join('') }
                : run);
        }
    });
    return out;
}
