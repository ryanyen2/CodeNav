/**
 * display-space.ts — the coordinate space every diff in codoc runs in.
 *
 * A `codoc:` citation is ONE position in the document and ~30 characters in the text
 * the store holds, and a hard break is one position and a newline. Diff two versions
 * of a paragraph in either of those spaces and the resulting offsets do not address
 * the other one, so every mark computed from them lands on the wrong words — or, with
 * the older defence in place, is dropped entirely, which is why a paragraph that cited
 * code used to show no change marks at all.
 *
 * DISPLAY SPACE is the shared answer: every inline atom is exactly one object-
 * replacement character. A char offset `i` inside a textblock at `pos` is then doc
 * position `pos + 1 + i`, citations included, and stored text and live nodes can be
 * compared directly.
 *
 * Split out of `webview/tiptap/display-text.ts` (which keeps the node-side half, since
 * that genuinely needs ProseMirror) so the pure model layer can reach it. That module
 * re-exports these, so its call sites are unchanged.
 */
import { REF_RE_SOURCE, boldMatches } from './pm-doc';

/** One char per inline atom — U+FFFC OBJECT REPLACEMENT CHARACTER. */
export const ATOM_CHAR = '￼';

/**
 * A stored/serialized paragraph projected into display space: inline
 * `[label](codoc:…)` citations and hard-break newlines collapse to ATOM_CHAR, and the
 * `**` markers around a bold span are dropped — matching what `paraDisplayText` yields
 * for the corresponding live block. External links stay literal, because the doc keeps
 * those as plain text.
 *
 * The bold markers are the reason this is a character walk rather than two regex
 * replacements. `textToInlineRuns` consumes `**…**` into a MARK, so the live block holds
 * `turns bodies back` while the store holds `**turns bodies back**`. Leaving the
 * asterisks in made every bolded description diff against itself forever: the word
 * diff matched the middle words and reported the two touching the markers as an
 * unsent human edit, so a feature the author had already saved kept pulsing blue on
 * `turns` and `back` with nothing left to send. Which markers to drop is not a regex
 * either — `boldMatches` is the function that decides what actually becomes a mark,
 * and asking it here is what keeps the two spaces from disagreeing again.
 */
export function mdDisplayText(s: string): string {
    const refs: Array<{ start: number; end: number }> = [];
    const re = new RegExp(REF_RE_SOURCE, 'g');
    for (let m = re.exec(s); m; m = re.exec(s)) {
        refs.push({ start: m.index, end: m.index + m[0].length });
    }
    const bolds = boldMatches(s, refs);
    if (!refs.length && !bolds.length) return s.replace(/\n/g, ATOM_CHAR);
    const refAt = new Map(refs.map(r => [r.start, r]));
    const marker = new Array<boolean>(s.length).fill(false);
    for (const b of bolds) {
        marker[b.start] = marker[b.start + 1] = marker[b.end - 2] = marker[b.end - 1] = true;
    }
    let out = '';
    for (let i = 0; i < s.length;) {
        const ref = refAt.get(i);
        if (ref) { out += ATOM_CHAR; i = ref.end; continue; }
        if (marker[i]) { i++; continue; }
        out += s[i] === '\n' ? ATOM_CHAR : s[i];
        i++;
    }
    return out;
}
