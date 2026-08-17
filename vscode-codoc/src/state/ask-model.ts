/**
 * ask-model.ts — the `/codoc:ask` walkthrough overlay, as data.
 *
 * A walkthrough is a numbered reading path over features that already exist
 * (`.codoc/ask.json`, written by the `codoc_walkthrough` MCP tool). It is a VIEW:
 * nothing here is authored state, nothing round-trips to the store, and
 * dismissing it leaves the tree byte-identical. That is what lets an ask happen
 * at any moment — mid-edit, mid-proposal, mid-realization — without a gate.
 *
 * DOM-free and pure so the parsing, the quote resolution, and the group runs are
 * unit-testable; the decorations live in `webview/tiptap/ask-decorations.ts` and
 * the bar in `webview/ask-bar.ts`.
 */

/** One stop on the path. `label` is computed by the writer (`codoc/loop/ask.py`),
 *  never by the model that produced the steps — `1a 1b / 2a` for a grouped path,
 *  `1 2 3` for an ungrouped one. */
export interface AskStep {
    label: string;
    /** The feature this stop lands on. A feature appears at most once per path. */
    feature_id: string;
    /** The procedure stage this stop belongs to; drawn once, above its first step. */
    group?: string;
    /** One line on what THIS node contributes to the answer. */
    note?: string;
    /** A span copied verbatim from the feature's title/description, to highlight.
     *  Verified against the store at write time, so a present quote resolves —
     *  but prose can change under it, so the renderer still tolerates a miss. */
    quote?: string;
    /** The code this stop points at, if any — turns the step into a jump. */
    file?: string;
    symbol?: string;
    line?: number;
}

export interface AskWalkthrough {
    id: string;
    question: string;
    answer: string;
    steps: AskStep[];
}

/** How long an overlay is honoured, mirroring `codoc/loop/ask.py:ASK_TTL_SECONDS`.
 *  The two clocks MUST agree: the reader would otherwise see a walkthrough the
 *  agent's own `codoc_walkthrough_read` says is gone. Keyed on file mtime, not the
 *  recorded time, so a clock change cannot resurrect one. */
export const ASK_TTL_MS = 8 * 3600 * 1000;

/** Tolerant parse of `.codoc/ask.json`. Returns null for anything that would not
 *  render — absent, wrong shape, or no steps — so every caller can treat null as
 *  "no overlay" rather than re-checking the pieces. */
export function parseAsk(raw: unknown): AskWalkthrough | null {
    if (!raw || typeof raw !== 'object') return null;
    const o = raw as Record<string, unknown>;
    const rawSteps = Array.isArray(o.steps) ? o.steps : [];
    const steps: AskStep[] = [];
    const seen = new Set<string>();
    for (const s of rawSteps) {
        if (!s || typeof s !== 'object') continue;
        const st = s as Record<string, unknown>;
        const fid = typeof st.feature_id === 'string' ? st.feature_id : '';
        // One chip per feature. The writer already enforces this; re-enforcing it
        // here means a hand-edited or older file cannot put two numbers on one row.
        if (!fid || seen.has(fid)) continue;
        seen.add(fid);
        steps.push({
            label: typeof st.label === 'string' ? st.label : String(steps.length + 1),
            feature_id: fid,
            group: typeof st.group === 'string' ? st.group : undefined,
            note: typeof st.note === 'string' ? st.note : undefined,
            quote: typeof st.quote === 'string' ? st.quote : undefined,
            file: typeof st.file === 'string' ? st.file : undefined,
            symbol: typeof st.symbol === 'string' ? st.symbol : undefined,
            line: typeof st.line === 'number' ? st.line : undefined,
        });
    }
    if (!steps.length) return null;
    return {
        id: typeof o.id === 'string' ? o.id : '',
        question: typeof o.question === 'string' ? o.question : '',
        answer: typeof o.answer === 'string' ? o.answer : '',
        steps,
    };
}

/** Step by feature id — the lookup every decoration does per heading. */
export function stepsByFid(walk: AskWalkthrough | null): Map<string, AskStep> {
    const out = new Map<string, AskStep>();
    for (const s of walk?.steps ?? []) out.set(s.feature_id, s);
    return out;
}

/** The fids whose step OPENS a group run — the only ones that draw the group
 *  heading. A group recurring later opens a new run, matching how the writer
 *  numbers it (`1a … 2a … 3a`), so the reader sees the stage named each time
 *  the procedure returns to it. */
export function groupOpeners(walk: AskWalkthrough | null): Map<string, string> {
    const out = new Map<string, string>();
    let prev: string | null = null;
    for (const s of walk?.steps ?? []) {
        const g = s.group ?? '';
        if (g && g !== prev) out.set(s.feature_id, g);
        prev = g;
    }
    return out;
}

/** Index of `fid` on the path, or -1 — drives "3 of 7" and the stepper. */
export function stepIndex(walk: AskWalkthrough | null, fid: string): number {
    return (walk?.steps ?? []).findIndex(s => s.feature_id === fid);
}

/**
 * Locate `quote` inside `haystack`, tolerating any difference in whitespace RUNS
 * (a description re-wrapped between the ask and the render still matches), and
 * return the range in the ORIGINAL string.
 *
 * Whitespace-tolerant rather than exact because the quote was verified against
 * the store's normalized prose while the renderer searches the editor's live
 * text, and the two agree on words but not always on where the lines break.
 * Returns null when the prose has genuinely moved on — the step then renders
 * without a highlight rather than highlighting the wrong words.
 */
export function findQuoteRange(haystack: string, quote: string): [number, number] | null {
    const needle = quote.trim().replace(/\s+/g, ' ');
    if (!needle) return null;

    // Normalized haystack + a map from each normalized char back to its original index.
    let norm = '';
    const at: number[] = [];
    let inRun = false;
    for (let i = 0; i < haystack.length; i++) {
        const ch = haystack[i];
        if (/\s/.test(ch)) {
            if (!inRun && norm.length) { norm += ' '; at.push(i); }
            inRun = true;
            continue;
        }
        inRun = false;
        norm += ch;
        at.push(i);
    }

    const hit = norm.indexOf(needle);
    if (hit === -1) return null;
    const start = at[hit];
    const lastNorm = hit + needle.length - 1;
    // The last matched char maps to its own start; the range ends after it. A
    // trailing normalized space stands for a whole run, so end at that run's start.
    const end = at[lastNorm] + (norm[lastNorm] === ' ' ? 0 : 1);
    return end > start ? [start, end] : null;
}
