/**
 * structure-commands.ts — tree-restructuring commands for the whole-doc editor
 * (slice 1). The document is a flat sequence of `featureHeading` nodes whose
 * `level` attr encodes tree depth; a heading "owns" the following blocks (its
 * description paragraphs) and the deeper headings beneath it (its subtree).
 *
 * Indent/outdent move a heading AND its whole subtree so the level sequence stays
 * tree-valid (a child is always exactly one deeper than its parent), matching how
 * `parseTreeToDoc` recovers depth from indentation. Pure-ish: each command takes
 * the editor state + dispatch, ProseMirror-style.
 */
import { Editor } from '@tiptap/core';
import { Node as PMModelNode, NodeType } from '@tiptap/pm/model';
import { TextSelection, Transaction, EditorState } from '@tiptap/pm/state';
import { newLocalId } from './local-id';
import { descriptionBlocks } from '../../state/pm-doc';

interface HeadingHit {
    node: PMModelNode;
    pos: number;       // position of the heading node
    index: number;     // index among top-level blocks
    level: number;
}

/** Top-level featureHeading nodes in document order. */
function headings(editor: Editor): HeadingHit[] {
    const out: HeadingHit[] = [];
    editor.state.doc.forEach((node, offset, index) => {
        if (node.type.name === 'featureHeading') {
            out.push({ node, pos: offset, index, level: Number(node.attrs.level) || 0 });
        }
    });
    return out;
}

/** Which heading owns the current selection (the nearest heading at or before it). */
function currentHeadingIndex(editor: Editor, hs: HeadingHit[]): number {
    const from = editor.state.selection.from;
    let idx = -1;
    for (let i = 0; i < hs.length; i++) {
        if (hs[i].pos <= from) idx = i;
        else break;
    }
    return idx;
}

/** The [start, end) index range of a heading's subtree (itself + deeper headings). */
function subtreeRange(hs: HeadingHit[], i: number): [number, number] {
    const level = hs[i].level;
    let end = i + 1;
    while (end < hs.length && hs[end].level > level) end++;
    return [i, end];
}

function shiftLevels(editor: Editor, hs: HeadingHit[], from: number, to: number, delta: number): boolean {
    const tr = editor.state.tr;
    for (let i = from; i < to; i++) {
        const h = hs[i];
        const newLevel = Math.max(0, h.level + delta);
        tr.setNodeMarkup(h.pos, undefined, { ...h.node.attrs, level: newLevel });
    }
    if (!tr.docChanged) return false;
    editor.view.dispatch(tr);
    return true;
}

/** Indent the current heading + its subtree under the preceding sibling (Tab). */
export function indentHeading(editor: Editor): boolean {
    const hs = headings(editor);
    const i = currentHeadingIndex(editor, hs);
    if (i <= 0) return false; // first heading can't indent
    // A valid new parent exists only if some earlier heading is at the same level.
    const level = hs[i].level;
    let hasParent = false;
    for (let j = i - 1; j >= 0; j--) {
        if (hs[j].level === level) { hasParent = true; break; }
        if (hs[j].level < level) break;
    }
    if (!hasParent) return false;
    const [start, end] = subtreeRange(hs, i);
    return shiftLevels(editor, hs, start, end, +1);
}

/** Outdent the current heading + its subtree (Shift-Tab). */
export function outdentHeading(editor: Editor): boolean {
    const hs = headings(editor);
    const i = currentHeadingIndex(editor, hs);
    if (i < 0 || hs[i].level === 0) return false;
    const [start, end] = subtreeRange(hs, i);
    return shiftLevels(editor, hs, start, end, -1);
}

/** Insert a new sibling feature heading right after the current heading's subtree.
 *  `realized: false` (the "plan" variant) marks it an explicit BUILD REQUEST so its ADD
 *  mints a realize directive, handed to the agent on mint (a plan ADD is one of loop_b's
 *  _EXPLICIT_REALIZE_KINDS) — born plan, so it is timing-safe (the very first settle that
 *  applies the ADD already carries realized=false; no toggle-after-create race, and no
 *  need for the diff to detect a realized transition on an existing node).
 *
 *  The flag only means anything because `featureUnits` now carries it onto the `add`
 *  payload; while it stopped at this heading attr, every plan node reached the daemon as
 *  an ordinary feature. */
export function newFeatureHeading(editor: Editor, opts: { realized?: boolean } = {}): boolean {
    const realized = opts.realized ?? true;
    const hs = headings(editor);
    const i = currentHeadingIndex(editor, hs);
    const level = i >= 0 ? hs[i].level : 0;
    // Insert position: end of the current subtree, or end of doc.
    let insertPos = editor.state.doc.content.size;
    if (i >= 0) {
        const [, end] = subtreeRange(hs, i);
        insertPos = end < hs.length ? hs[end].pos : editor.state.doc.content.size;
    }
    const placeholder = realized ? 'New feature' : 'New feature (plan)';
    const heading = editor.schema.nodes.featureHeading.create(
        { fid: null, level, retired: false, realized, localId: newLocalId() },
        editor.schema.text(placeholder),
    );
    const para = editor.schema.nodes.paragraph.create();
    const tr = editor.state.tr.insert(insertPos, [heading, para]);
    // SELECT the placeholder title (not just place the caret) so the user types over it
    // immediately — no manual select-all, no "New featureMy title" concatenation.
    const titleStart = insertPos + 1;
    tr.setSelection(TextSelection.create(tr.doc, titleStart, titleStart + placeholder.length));
    editor.view.dispatch(tr);
    editor.view.focus();
    return true;
}

/**
 * The transform behind the `#{n} ` heading input rule (U2). PURE + unit-tested.
 *
 * `[start, end)` is the matched `#{n} ` range. Two cases, so the gesture is uniform
 * across all four levels and never silently becomes literal text:
 *  - **At block start** (no preceding text): convert the current block in place.
 *  - **Preceding text** (typed `## ` at the end of / within a populated paragraph,
 *    the reported bug): SPLIT at the caret — the text before stays as the prior
 *    feature's paragraph, and a NEW empty featureHeading begins after it. The
 *    author then types the title. No content is consumed from the previous feature.
 *
 * A fresh `localId` (KTD8) is minted onto the new heading so it has stable identity
 * before the daemon assigns a `fid`. Returns the transaction, or null if the schema
 * lacks the node (defensive).
 */
export function headingFromInputRule(
    state: EditorState, level: number, start: number, end: number, localId?: string,
): Transaction | null {
    const type: NodeType | undefined = state.schema.nodes.featureHeading;
    if (!type) return null;
    const attrs = { fid: null, level, retired: false, realized: true, localId: localId ?? newLocalId() };
    const $start = state.doc.resolve(start);
    const blockStart = $start.start();
    const tr = state.tr.delete(start, end);
    if (start === blockStart) {
        // Block start → convert this (empty/own-line) block in place to a heading.
        return tr.setBlockType(start, start, type, attrs);
    }
    // Preceding text → keep the current paragraph as the prior feature's description
    // and INSERT a fresh empty heading right after it (no content is consumed). The
    // caret lands inside the new heading so the author types the title.
    const $after = tr.doc.resolve(start);
    const insertAt = $after.after($after.depth);
    tr.insert(insertAt, type.create(attrs));
    tr.setSelection(TextSelection.create(tr.doc, insertAt + 1));
    return tr;
}

/** Toggle the retired flag on the current heading (→ `~` marker → RETIRE on commit). */
export function toggleRetireHeading(editor: Editor): boolean {
    const hs = headings(editor);
    const i = currentHeadingIndex(editor, hs);
    if (i < 0) return false;
    const h = hs[i];
    const tr = editor.state.tr.setNodeMarkup(h.pos, undefined, { ...h.node.attrs, retired: !h.node.attrs.retired });
    editor.view.dispatch(tr);
    return true;
}

// DELETED: togglePlanHeading. It was exported, bound to nothing, and could not have
// worked if it had been: `realized` rides the command channel only on an ADD, so
// toggling the flag on a heading the daemon has already minted reaches nothing — the
// button would have promised a build and produced silence. On a not-yet-minted heading
// it would merely race `newFeatureHeading({realized:false})`, which sets the same flag
// at birth with no window in which a settle can emit the ADD without it. The `◇ plan`
// toolbar button is the whole gesture; there is deliberately no plan counterpart to
// toggleRetireHeading above.

/** Find the DOM position of a feature heading by fid (for tree-pane navigation). */
export function headingPosForFid(editor: Editor, fid: string): number | null {
    let found: number | null = null;
    editor.state.doc.forEach((node, offset) => {
        if (found === null && node.type.name === 'featureHeading' && node.attrs.fid === fid) found = offset;
    });
    return found;
}

/**
 * Put a feature's description back to `text`, as an ordinary edit of the document.
 *
 * This is the whole of "Restore mine". The verdict on a loop rewrite used to emit a
 * `set_description` from the host and change nothing on screen — the store still held
 * the loop's wording, the projection kept re-rendering it, and a button labelled Restore
 * visibly did nothing. Writing the words back HERE makes it the same kind of event as
 * typing them: `onUpdate` marks the doc dirty, the ordinary settle emits the command
 * against a baseline the editor can vouch for, the ink shows it as the reader's own, and
 * the held-draft gate still decides whether it ever reaches an agent.
 *
 * Returns false when the fid is not in the document, so a stale verdict is a no-op
 * rather than an edit somewhere unrelated.
 */
export function restoreFeatureDescription(editor: Editor, fid: string, text: string): boolean {
    const hs = headings(editor);
    const i = hs.findIndex(h => (h.node.attrs.fid as string | null) === fid);
    if (i < 0) return false;
    const start = hs[i].pos + hs[i].node.nodeSize;
    const end = i + 1 < hs.length ? hs[i + 1].pos : editor.state.doc.content.size;
    const blocks = descriptionBlocks(text, fid).map(b => editor.schema.nodeFromJSON(b));
    editor.view.dispatch(editor.state.tr.replaceWith(start, end, blocks));
    return true;
}
