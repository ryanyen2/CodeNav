/**
 * window.ts — the timeline window as the daemon publishes it (`history.json`),
 * and two pure readers over it: which batch undo or redo should invert next,
 * and what every block said at a position in the window (09-editor §4, §7).
 *
 * Undo is exact and shared with the store: a batch is undone by submitting
 * the recorded inverses of its ops, newest first, as a new batch named
 * `u-<batch>`; redo undoes that batch (`u-u-<batch>`). A batch is undone when
 * a later `u-` batch of it exists. Mod-z picks the newest of the viewer's
 * batches with an even number of `u-` prefixes that is not undone; redo, odd.
 */
import type { OpBody } from './projection';

export interface WindowEntry {
    pos: number;
    id: string;
    at: string;
    actor: string;
    kind: string;
    batch: string;
    /** Where this batch began, so a batch that reaches back past the window is
     *  recognisable however its ops interleave with another actors'. */
    batch_first?: number;
    plan: string | null;
    caused_by: string | null;
    blocks: Record<string, string>;     // text of each touched block right after the op
    inverse: OpBody[];
}

export interface HistoryWindow {
    start: number;
    head: number;
    before: Record<string, string>;     // text of each touched block before the window
    /** Blocks created inside the window: they have no "before". */
    born?: string[];
    /** Rank per block the window names, live or not, so a past page can be
     *  rendered in the order it had. */
    ranks?: Record<string, string>;
    ops: WindowEntry[];
}

function depth(batch: string): number {
    let d = 0;
    while (batch.startsWith('u-', d * 2)) d++;
    return d;
}

/** The ops to submit for the viewer's next undo (`redo=false`) or redo, with
 *  the batch id they go under; null when there is nothing to invert. */
export function nextUndo(w: HistoryWindow, actor: string, redo = false): { batch: string; ops: OpBody[] } | null {
    const batches = new Map<string, WindowEntry[]>();
    for (const e of w.ops) if (e.actor === actor) (batches.get(e.batch) ?? batches.set(e.batch, []).get(e.batch)!).push(e);
    const undone = new Set([...batches.keys()].filter(b => b.startsWith('u-')).map(b => b.slice(2)));
    const candidates = [...batches.entries()]
        .filter(([b]) => (depth(b) % 2 === 1) === redo && !undone.has(b))
        .sort((a, b) => b[1][b[1].length - 1].pos - a[1][a[1].length - 1].pos);
    for (const [batch, entries] of candidates) {
        // A batch that began before the window is only half here; undoing that
        // half would leave the other half standing.
        const first = entries[0].batch_first ?? (entries[0].pos === w.start ? w.start - 1 : entries[0].pos);
        if (w.start > 0 && first < w.start) continue;
        const ops = [...entries].reverse().flatMap(e => e.inverse);
        if (ops.length) return { batch: `u-${batch}`, ops };
    }
    return null;
}

const BIRTHS = new Set(['block.add', 'block.split']);

/** What block `bid` said right after window position `pos` (an index into
 *  `w.ops`, -1 for before the window); null when the block did not exist yet. */
export function textAt(w: HistoryWindow, bid: string, pos: number, live: string): string | null {
    for (let i = Math.min(pos, w.ops.length - 1); i >= 0; i--) {
        const t = w.ops[i].blocks[bid];
        if (t !== undefined) return t;
    }
    // Nothing at or before `pos`: either the block was born later in the window
    // (it did not exist then), or it predates the window untouched.
    const born = new Set(w.born ?? []);
    const laterBirth = w.ops.slice(pos + 1).some(e => BIRTHS.has(e.kind) && bid in e.blocks
                                                 && (born.has(bid) || !(bid in w.before)));
    if (laterBirth) return null;
    return bid in w.before ? w.before[bid] : live;
}

/** A display-only word diff between two texts: [kind, text] pieces. */
export function wordDiff(a: string, b: string): Array<['eq' | 'del' | 'ins', string]> {
    const A = a.split(/(\s+)/).filter(Boolean), B = b.split(/(\s+)/).filter(Boolean);
    const n = A.length, m = B.length;
    const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    const out: Array<['eq' | 'del' | 'ins', string]> = [];
    let i = 0, j = 0;
    const push = (k: 'eq' | 'del' | 'ins', t: string) => { const last = out[out.length - 1]; if (last && last[0] === k) last[1] += t; else out.push([k, t]); };
    while (i < n && j < m) {
        if (A[i] === B[j]) { push('eq', A[i]); i++; j++; }
        else if (dp[i + 1][j] >= dp[i][j + 1]) push('del', A[i++]);
        else push('ins', B[j++]);
    }
    while (i < n) push('del', A[i++]);
    while (j < m) push('ins', B[j++]);
    return out;
}
