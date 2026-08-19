/**
 * para-align.ts — pairing two versions of a description's paragraphs.
 *
 * Extracted from `webview/tiptap/display-text.ts`, which needed it for the same
 * reason and got there first. It moved because pairing paragraphs is a fact about
 * TEXT, and the module it lived in imports ProseMirror — so a pure consumer
 * (settlement.ts) could not reach it without pulling the editor into the model
 * layer. `display-text.ts` re-exports it, so its call sites are unchanged.
 *
 * The rule it exists to enforce: paragraphs are paired by CONTENT, never by index.
 * One paragraph inserted at the top of a description shifts every index below it,
 * and an index-paired diff then reports the whole rest of the node as rewritten —
 * which is both wrong and, on a surface whose job is to say what changed, actively
 * misleading.
 */

/** Word-token Dice similarity — cheap, order-free, punctuation-insensitive. */
function sim(a: string, b: string): number {
    if (a === b) return 1;
    const tok = (s: string): Set<string> =>
        new Set(s.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean));
    const ta = tok(a), tb = tok(b);
    if (!ta.size || !tb.size) return 0;
    let hit = 0;
    for (const t of ta) if (tb.has(t)) hit++;
    return (2 * hit) / (ta.size + tb.size);
}

/** An off-by-one paragraph reads as an insert/delete only when the cross pairing
 *  is clearly better than the straight one — below this, treat as in-place edit. */
const SIM_ANCHOR = 0.5;

/**
 * Pair current paragraphs with baseline paragraphs so an inserted or removed
 * paragraph doesn't shift every later diff onto the wrong neighbour.
 *
 * Equal paragraphs anchor exactly; when the heads differ, an exact match found
 * later on either side resolves the offset (insertions pair with null, deleted
 * baseline paragraphs are skipped). When neither side has an exact anchor —
 * an inserted paragraph NEXT TO an edited one — token similarity arbitrates:
 * if the baseline head clearly matches the NEXT current paragraph better, the
 * current head was inserted; if the NEXT baseline paragraph clearly matches the
 * current head better, the baseline head was deleted. Otherwise the heads are
 * an in-place edit. Returns, per current index, the baseline index — or null
 * for an inserted paragraph (diff against '').
 */
export function alignParas(base: string[], cur: string[]): Array<number | null> {
    const out: Array<number | null> = [];
    let i = 0;
    let j = 0;
    while (j < cur.length) {
        if (i >= base.length) { out.push(null); j++; continue; }
        if (base[i] === cur[j]) { out.push(i); i++; j++; continue; }
        const del = base.indexOf(cur[j], i + 1);
        const ins = cur.indexOf(base[i], j + 1);
        if (del !== -1 && (ins === -1 || del - i <= ins - j)) { out.push(del); i = del + 1; j++; continue; }
        if (ins !== -1) { out.push(null); j++; continue; }
        const sHere = sim(base[i], cur[j]);
        const sInserted = j + 1 < cur.length ? sim(base[i], cur[j + 1]) : 0;
        const sDeleted = i + 1 < base.length ? sim(base[i + 1], cur[j]) : 0;
        if (sInserted > sHere && sInserted >= SIM_ANCHOR) { out.push(null); j++; continue; }
        if (sDeleted > sHere && sDeleted >= SIM_ANCHOR) { i++; continue; }  // re-try this j
        out.push(i); i++; j++;
    }
    return out;
}

/**
 * Baseline paragraphs with no counterpart in `cur`, each reported with the CURRENT
 * block it should be anchored to — the first current paragraph that comes after it,
 * or `null` for a trailing removal (anchor at the end of the last block).
 *
 * A whole paragraph deleted is the one change an alignment naturally makes invisible:
 * it survives in neither the pairing nor any current block's diff, so without this the
 * surface silently reports that nothing happened.
 */
export function orphans(
    base: string[], cur: string[], pairing: Array<number | null>,
): { baseIndex: number; anchorIndex: number | null }[] {
    const matched = new Set(pairing.filter((x): x is number => x !== null));
    const out: { baseIndex: number; anchorIndex: number | null }[] = [];
    for (let b = 0; b < base.length; b++) {
        if (matched.has(b)) continue;
        let anchor: number | null = null;
        for (let c = 0; c < cur.length; c++) {
            const p = pairing[c];
            if (p !== null && p > b) { anchor = c; break; }
        }
        out.push({ baseIndex: b, anchorIndex: anchor });
    }
    return out;
}
