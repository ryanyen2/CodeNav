/**
 * doc-diff.ts — string diff between two texts (pure, testable).
 *
 * The diff model is "tokenized look, block-level store": a suggestion stores the
 * settled `old` text and the proposed `new` text; the editor renders the change as
 * an inline diff (struck deletions, highlighted insertions). Two granularities are
 * offered from one LCS core:
 *   - `wordDiff`     — whitespace tokens (fine-grained, every changed word).
 *   - `sentenceDiff` — sentence tokens (one accept/reject per changed sentence, the
 *                      low-burden default for agent review; see agent-proposals.ts).
 */
export type DiffOp = 'same' | 'del' | 'ins';

export interface DiffRun {
    t: DiffOp;
    s: string;
}

/**
 * Token-level LCS diff over a pre-tokenized pair, returning merged same/del/ins
 * runs (adjacent runs of the same op coalesce). Tokens are compared by exact
 * equality and concatenate losslessly, so the original strings round-trip from the
 * runs regardless of the tokenizer. Shared by wordDiff and sentenceDiff.
 */
function tokenDiff(a: string[], b: string[]): DiffRun[] {
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

/** One "word" per script. Latin (and everything else spaced) tokenizes on
 *  whitespace runs; CJK ideographs, kana, hangul and fullwidth punctuation are
 *  each their own token, because those scripts put no spaces between words. The
 *  whitespace-only split read an entire Chinese paragraph as ONE token, so any
 *  mid-paragraph edit diffed as "the whole paragraph changed" — a pilot typing
 *  five English words into a Chinese description watched the entire node light
 *  up as changed. (The Python side made the same correction once already:
 *  doclang's `terms`/`tokens` segment per script for exactly this reason.) */
const CJK = '\\u2E80-\\u9FFF\\uF900-\\uFAFF\\u3040-\\u30FF\\uAC00-\\uD7AF\\u3000-\\u303F\\uFF00-\\uFFEF';
const WORD_TOKENS = new RegExp(`\\s+|[${CJK}]|[^\\s${CJK}]+`, 'gu');

export function wordTokens(s: string): string[] {
    return String(s).match(WORD_TOKENS) ?? [];
}

/**
 * Word-level diff: script-aware tokens (see `wordTokens`), whitespace preserved
 * as its own tokens so spacing round-trips. Adjacent runs of the same op merge.
 */
export function wordDiff(oldStr: string, newStr: string): DiffRun[] {
    return tokenDiff(wordTokens(oldStr), wordTokens(newStr));
}

/**
 * Split a string into sentences, each keeping its trailing delimiter + whitespace
 * so the parts concatenate back to the input exactly. A sentence ends at one or more
 * of `. ! ?` that is followed by whitespace or end-of-string — so a mid-token dot
 * ("3.11", a `codoc:` ref) does NOT split — or at fullwidth CJK terminators
 * (`。！？；`), which are never followed by a space because those scripts do not
 * write one: requiring whitespace after the stop read an entire Chinese
 * description as ONE sentence, so the agent's reflected change diffed as "the
 * whole node changed". A string with no boundary is one sentence. Conservative by
 * design (an abbreviation like "e.g." may over-split — the cost is one extra
 * accept/reject unit, never a wrong diff).
 */
export function sentenceSplit(s: string): string[] {
    const str = String(s);
    if (str === '') return [];
    const out: string[] = [];
    // A sentence chunk: minimal text up to boundary punctuation (Latin stops need a
    // following space/end; CJK stops are boundaries on their own), plus trailing
    // whitespace; or the final remainder with no boundary punctuation.
    const re = /.*?(?:[.!?]+(?=\s|$)|[\u3002\uFF01\uFF1F\uFF1B]+)\s*|.+$/gs;
    let m: RegExpExecArray | null;
    while ((m = re.exec(str)) !== null) {
        if (m[0] === '') { re.lastIndex++; continue; } // guard against a zero-width match
        out.push(m[0]);
    }
    return out.length ? out : [str];
}

/**
 * Sentence tokens for diffing: like sentenceSplit, but the trailing whitespace of
 * each sentence is peeled into its own token (the same trick wordDiff uses for spaces).
 * This keeps a sentence *core* identical whether or not a neighbour follows it — so
 * removing the final sentence diffs as one deletion, not a spurious del+ins on the
 * sentence that lost its trailing space — and makes each changed sentence its own
 * del+ins unit (one accept/reject per sentence).
 */
function sentenceTokens(s: string): string[] {
    const out: string[] = [];
    for (const chunk of sentenceSplit(s)) {
        const ws = /\s+$/.exec(chunk);
        if (ws && ws[0].length < chunk.length) {
            out.push(chunk.slice(0, chunk.length - ws[0].length)); // sentence core
            out.push(ws[0]);                                        // trailing whitespace
        } else {
            out.push(chunk); // no trailing whitespace
        }
    }
    return out;
}

/**
 * Sentence-level diff: LCS over sentence cores (whitespace peeled out as its own
 * tokens). A changed sentence surfaces as one `del` (its old form) + one `ins` (its
 * new form), separated from neighbours by unchanged whitespace. This is the low-burden
 * granularity for agent-edit review — one accept/reject per changed sentence, not per word.
 */
export function sentenceDiff(oldStr: string, newStr: string): DiffRun[] {
    return tokenDiff(sentenceTokens(String(oldStr)), sentenceTokens(String(newStr)));
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
