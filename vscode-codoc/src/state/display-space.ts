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
import { REF_RE_SOURCE } from './pm-doc';

/** One char per inline atom — U+FFFC OBJECT REPLACEMENT CHARACTER. */
export const ATOM_CHAR = '￼';

/**
 * A stored/serialized paragraph projected into display space: inline
 * `[label](codoc:…)` citations and hard-break newlines collapse to ATOM_CHAR —
 * matching what `paraDisplayText` yields for the corresponding live block.
 * (External links and emphasis stay literal: the doc keeps them as plain text.)
 */
export function mdDisplayText(s: string): string {
    return s.replace(new RegExp(REF_RE_SOURCE, 'g'), ATOM_CHAR).replace(/\n/g, ATOM_CHAR);
}
