/**
 * threads.ts — pure assembly of one feature's unified dependency "threads" (U4/H6).
 *
 * Kept out of the vscode-coupled host (tree-editor.ts) so the dedup / empty / self-edge
 * rules are unit-testable. COLOUR/SHAPE rendering lives in the webview; this is only the
 * data merge: feature→feature edges (reads / used-by) + code-ref bindings.
 */
import type { ThreadsData } from '../webview/protocol';

export interface ThreadsInput {
    /** out-edges — features this one depends on (→ reads). */
    out: { to: string }[];
    /** in-edges — features that depend on this one (→ used by). */
    in: { to: string }[];
    /** bound code symbols for this feature (→ code refs). */
    bindings: { file: string; symbol: string }[];
    /** resolve a feature id → its title; an empty title drops the edge. */
    titleOf: (fid: string) => string;
    /** this feature's own id, to drop self-edges. */
    selfId: string;
}

/**
 * Assemble one feature's threads. `reads` and `usedBy` each dedup WITHIN their own strand
 * (a mutual dependency may legitimately appear in both); self-edges and title-less edges
 * are dropped; order is preserved. Returns `null` when all three strands are empty, so the
 * caller omits the line entirely.
 */
export function assembleThreads(input: ThreadsInput): ThreadsData | null {
    const targets = (edges: { to: string }[]): { toId: string; toTitle: string }[] => {
        const seen = new Set<string>();
        const out: { toId: string; toTitle: string }[] = [];
        for (const e of edges) {
            const toTitle = input.titleOf(e.to);
            if (!toTitle || e.to === input.selfId || seen.has(e.to)) continue;
            seen.add(e.to);
            out.push({ toId: e.to, toTitle });
        }
        return out;
    };
    const reads = targets(input.out);
    const usedBy = targets(input.in);
    const refs = input.bindings.map(b => ({ file: b.file, symbol: b.symbol }));
    if (!reads.length && !usedBy.length && !refs.length) return null;
    return { reads, usedBy, refs };
}
