/**
 * block-boundary.ts — a feature's title and its prose are different things, and
 * no keystroke may quietly turn one into the other.
 *
 * `featureHeading` and `paragraph` both hold `inline*`, so ProseMirror considers
 * them joinable and its default `joinBackward` is happy to merge them. That makes
 * Backspace at the start of a description's first paragraph append the prose to
 * the feature's TITLE — and Delete at the end of a paragraph swallow the next
 * feature's heading into it, destroying that node's identity. Both are one
 * keystroke away at all times, and both settle as a legitimate-looking rename.
 *
 * The rule: **a deletion at a block boundary never merges content across a
 * heading.** When it would, the caret moves across the boundary instead and the
 * text stays where it is. The gesture still feels alive — the caret goes where
 * the user was heading — and a second press then edits the title's last
 * character, which is a real title edit and means exactly what it looks like.
 *
 * Removing an EMPTY block is still allowed: nothing merges, so nothing is at
 * risk, and the user keeps a way to clean up stray blank lines.
 *
 * The decision is a pure function of the state so it can be tested headlessly,
 * away from the browser where these gestures actually originate.
 */
import type { EditorState, Transaction } from '@tiptap/pm/state';
import { TextSelection } from '@tiptap/pm/state';

const HEADING = 'featureHeading';

/** `allow` — let ProseMirror's default run. `move` — put the caret here instead. */
export type BoundaryVerdict = { kind: 'allow' } | { kind: 'move'; pos: number };

const ALLOW: BoundaryVerdict = { kind: 'allow' };

/** Shared preconditions: a collapsed caret sitting in a top-level block. */
function topLevelCaret(state: EditorState): { index: number; pos: number } | null {
    const { selection } = state;
    if (!selection.empty) return null;   // an explicit selection delete is a stated intent
    const { $from } = selection;
    if ($from.depth !== 1) return null;
    return { index: $from.index(0), pos: $from.before(1) };
}

/** Backspace / delete-word-back / delete-to-line-start at the START of a block. */
export function backspaceVerdict(state: EditorState): BoundaryVerdict {
    const at = topLevelCaret(state);
    if (!at) return ALLOW;
    const { $from } = state.selection;
    if ($from.parentOffset !== 0) return ALLOW;   // deleting within the block
    if (at.index === 0) return ALLOW;             // nothing before to merge into

    const cur = $from.parent;
    const prev = state.doc.child(at.index - 1);
    if (cur.type.name !== HEADING && prev.type.name !== HEADING) return ALLOW;
    if (cur.content.size === 0) return ALLOW;     // removing an empty block merges nothing

    const prevStart = at.pos - prev.nodeSize;
    return { kind: 'move', pos: prevStart + 1 + prev.content.size };
}

/** Forward-delete at the END of a block (the same merge, approached from below). */
export function deleteForwardVerdict(state: EditorState): BoundaryVerdict {
    const at = topLevelCaret(state);
    if (!at) return ALLOW;
    const { $from } = state.selection;
    const cur = $from.parent;
    if ($from.parentOffset !== cur.content.size) return ALLOW;
    if (at.index >= state.doc.childCount - 1) return ALLOW;

    const next = state.doc.child(at.index + 1);
    if (cur.type.name !== HEADING && next.type.name !== HEADING) return ALLOW;
    if (next.content.size === 0) return ALLOW;

    return { kind: 'move', pos: at.pos + cur.nodeSize + 1 };
}

/**
 * Turn a verdict into the transaction to dispatch, or null to let the default run.
 * Selection-only and kept out of the undo history: moving the caret is not an
 * edit, and it must not become a step the user has to undo twice past.
 */
export function verdictTransaction(state: EditorState, verdict: BoundaryVerdict): Transaction | null {
    if (verdict.kind !== 'move') return null;
    const pos = Math.max(0, Math.min(verdict.pos, state.doc.content.size));
    return state.tr
        .setSelection(TextSelection.near(state.doc.resolve(pos)))
        .setMeta('addToHistory', false);
}
