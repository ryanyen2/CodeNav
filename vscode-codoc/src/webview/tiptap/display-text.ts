/**
 * display-text.ts — the DISPLAY-SPACE contract for baseline↔current diffs.
 *
 * The misplaced-decoration bugs shared one root cause: the diff ran in a text
 * space whose character offsets did not match ProseMirror document positions.
 * A codeRef chip occupies exactly ONE doc position, but contributed zero chars
 * via `textContent` and ~30 chars via its markdown serialization — so any
 * paragraph holding a chip either mis-anchored its underline or (the old
 * defense) lost it entirely. And pairing paragraphs by INDEX meant one inserted
 * or deleted paragraph shifted every later diff onto the wrong neighbour.
 *
 * The contract here: both sides of a diff are projected into *display text*,
 * where every inline atom (codeRef chip, hard break) is one object-replacement
 * char — so char index i inside a textblock maps to doc position `pos + 1 + i`,
 * always, chips included. Paragraph lists are paired by exact-match-anchored
 * alignment, not index.
 */
import { Node as PMModelNode } from '@tiptap/pm/model';
import { REF_RE_SOURCE } from '../../state/pm-doc';

/** One char per inline atom — U+FFFC OBJECT REPLACEMENT CHARACTER. */
export const ATOM_CHAR = '￼';

/**
 * A textblock's display text: text runs verbatim, every non-text inline node as
 * ATOM_CHAR × its nodeSize. Length always equals `node.content.size`, so a char
 * offset maps 1:1 onto document positions inside the block.
 */
export function paraDisplayText(node: PMModelNode): string {
    let s = '';
    node.forEach(child => {
        s += child.isText ? (child.text ?? '') : ATOM_CHAR.repeat(child.nodeSize);
    });
    return s;
}

/**
 * A stored/serialized paragraph projected into the same display space: inline
 * `[label](codoc:…)` citations and hard-break newlines collapse to ATOM_CHAR —
 * matching what `paraDisplayText` yields for the corresponding live block.
 * (External links and emphasis stay literal: the doc keeps them as plain text.)
 */
export function mdDisplayText(s: string): string {
    return s.replace(new RegExp(REF_RE_SOURCE, 'g'), ATOM_CHAR).replace(/\n/g, ATOM_CHAR);
}

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
