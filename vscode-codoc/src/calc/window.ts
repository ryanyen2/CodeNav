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
    plan: string | null;
    caused_by: string | null;
    blocks: Record<string, string>;     // text of each touched block right after the op
    inverse: OpBody[];
}

export interface HistoryWindow {
    start: number;
    head: number;
    before: Record<string, string>;     // text of each touched block before the window
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
        if (w.start > 0 && entries[0].pos === w.start) continue;   // the batch may reach past the window
        const ops = [...entries].reverse().flatMap(e => e.inverse);
        if (ops.length) return { batch: `u-${batch}`, ops };
    }
    return null;
}

/** What block `bid` said right after window position `pos` (an index into
 *  `w.ops`, -1 for before the window); null when the block did not exist yet. */
export function textAt(w: HistoryWindow, bid: string, pos: number, live: string): string | null {
    for (let i = Math.min(pos, w.ops.length - 1); i >= 0; i--) {
        const t = w.ops[i].blocks[bid];
        if (t !== undefined) return t;
    }
    if (bid in w.before) return w.before[bid];
    // Never touched at or before pos: it either predates the window unchanged
    // (live is right) or is added later in the window (not there yet).
    return w.ops.slice(pos + 1).some(e => e.kind === 'block.add' && bid in e.blocks) ? null : live;
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
