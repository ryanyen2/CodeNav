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
import { Node as PMModelNode } from '@tiptap/pm/model';
import { TextSelection } from '@tiptap/pm/state';

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

/** Insert a new sibling feature heading right after the current heading's subtree. */
export function newFeatureHeading(editor: Editor): boolean {
    const hs = headings(editor);
    const i = currentHeadingIndex(editor, hs);
    const level = i >= 0 ? hs[i].level : 0;
    // Insert position: end of the current subtree, or end of doc.
    let insertPos = editor.state.doc.content.size;
    if (i >= 0) {
        const [, end] = subtreeRange(hs, i);
        insertPos = end < hs.length ? hs[end].pos : editor.state.doc.content.size;
    }
    const heading = editor.schema.nodes.featureHeading.create(
        { fid: null, level, retired: false, realized: true },
        editor.schema.text('New feature'),
    );
    const para = editor.schema.nodes.paragraph.create();
    const tr = editor.state.tr.insert(insertPos, [heading, para]);
    // Put the cursor in the new heading's title.
    tr.setSelection(TextSelection.near(tr.doc.resolve(insertPos + 1)));
    editor.view.dispatch(tr);
    editor.view.focus();
    return true;
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

/** Find the DOM position of a feature heading by fid (for tree-pane navigation). */
export function headingPosForFid(editor: Editor, fid: string): number | null {
    let found: number | null = null;
    editor.state.doc.forEach((node, offset) => {
        if (found === null && node.type.name === 'featureHeading' && node.attrs.fid === fid) found = offset;
    });
    return found;
}
