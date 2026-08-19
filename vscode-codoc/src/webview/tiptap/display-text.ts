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
// The display-space contract itself is pure text, so it lives in the model layer where
// settlement.ts and the host can reach it; only the NODE side below needs ProseMirror.
export { ATOM_CHAR, mdDisplayText } from '../../state/display-space';
import { ATOM_CHAR } from '../../state/display-space';
// Paragraph pairing is a fact about text, not about ProseMirror, so it lives in the
// model layer where the pure consumers (settlement.ts) can reach it. Re-exported here
// because this module is where every existing call site already looks for it.
export { alignParas, orphans } from '../../state/para-align';


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
