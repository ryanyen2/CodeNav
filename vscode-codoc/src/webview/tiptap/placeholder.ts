/**
 * placeholder.ts — what an empty document, and an empty description, should say.
 *
 * The editor had no empty state at all. A fresh tree opened as a blank page, and
 * an empty description under a heading was an empty line — so the two authoring
 * affordances that exist (`/` for a typed-media block, `@` for a code reference)
 * were undiscoverable by anyone who had not read the docs. The tree pane's
 * message ("Run `codoc init`") does not cover either case, and on the hub a
 * remote contributor cannot run `codoc init` at all.
 *
 * Two placeholders, because they answer different questions:
 *
 *   • The DOCUMENT is empty — "there is nothing here yet, and here is how the
 *     first thing gets made". Shown unconditionally: nobody has to guess whether
 *     they clicked in the right place.
 *   • A DESCRIPTION is empty — "this feature has no prose". Shown only on the
 *     block holding the caret, the way every text editor worth using does it.
 *     Showing it on every empty block at once turns a document into a form.
 *
 * Placeholders are decorations with CSS `::before` content, so no text is ever in
 * the document: nothing to serialize into `tree.codoc`, nothing to accidentally
 * settle, nothing a select-all can copy.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import type { EditorState } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';

const placeholderKey = new PluginKey('codocPlaceholder');

export const EMPTY_DOC_HINT = 'Describe a feature. Type ⌘K to add one, or / for a diagram, image or link.';
export const EMPTY_BLOCK_HINT = 'Add what this feature does, and why. / for a block, @ to cite code.';
export const EMPTY_TITLE_HINT = 'Name this feature';

/** True when the document holds no feature and no prose — not merely one empty
 *  paragraph left behind by a delete, but genuinely nothing authored. */
export function isEmptyDocument(state: EditorState): boolean {
    const { doc } = state;
    if (doc.childCount === 0) return true;
    let authored = false;
    doc.forEach(node => {
        if (node.type.name === 'featureHeading') authored = true;
        else if (node.content.size > 0) authored = true;
    });
    return !authored;
}

/** The prompt this state should show, and where — or null for none.
 *
 *  Pure, and the whole of the decision: `buildPlaceholders` is a thin shell that
 *  turns it into a decoration. Keeping the rule out here is what lets it be
 *  tested directly, since the suite runs node-env with no DOM. */
export function placeholderFor(state: EditorState): { pos: number; text: string } | null {
    const { doc, selection } = state;

    if (isEmptyDocument(state)) {
        // Anchor to the first block if there is one; an utterly empty document
        // has no block to hang a decoration on, and the shell's own empty state
        // already covers that case.
        let found: { pos: number; text: string } | null = null;
        doc.forEach((_node, pos) => {
            if (!found) found = { pos, text: EMPTY_DOC_HINT };
        });
        return found;
    }

    // Only the block holding the caret, and only when the selection is collapsed:
    // during a selection the reader is acting on text, not looking for a prompt.
    if (!selection.empty) return null;
    const $pos = selection.$from;
    if ($pos.depth === 0) return null;
    const node = $pos.parent;
    if (node.content.size > 0) return null;

    return {
        pos: $pos.before($pos.depth),
        text: node.type.name === 'featureHeading' ? EMPTY_TITLE_HINT : EMPTY_BLOCK_HINT,
    };
}

export function buildPlaceholders(state: EditorState): DecorationSet {
    const hint = placeholderFor(state);
    if (!hint) return DecorationSet.empty;
    const node = state.doc.nodeAt(hint.pos);
    if (!node) return DecorationSet.empty;
    return DecorationSet.create(state.doc, [
        Decoration.node(hint.pos, hint.pos + node.nodeSize, {
            class: hint.text === EMPTY_DOC_HINT ? 'ce-placeholder ce-placeholder-doc' : 'ce-placeholder',
            'data-placeholder': hint.text,
        }),
    ]);
}

export function placeholderPlugin(): Plugin {
    return new Plugin({
        key: placeholderKey,
        state: {
            init: (_c, state) => buildPlaceholders(state),
            // Depends on the SELECTION as well as the doc, so it cannot use the
            // structure-keyed decoration policy — a caret move with no doc change
            // is exactly when this has to be recomputed. Cheap: at most one
            // decoration, and no DOM is built here.
            apply: (tr, old, _o, newState) =>
                (tr.docChanged || tr.selectionSet) ? buildPlaceholders(newState) : old,
        },
        props: { decorations(state) { return placeholderKey.getState(state); } },
    });
}

export const Placeholder = Extension.create({
    name: 'codocPlaceholder',
    addProseMirrorPlugins() { return [placeholderPlugin()]; },
});
