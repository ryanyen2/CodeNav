/**
 * mark-hygiene.ts — the user's words are never the agent's words.
 *
 * Agent amends are materialized as the tracked-changes engine's `insertion` /
 * `deletion` marks on the projected doc (`state/agent-proposals.ts`). Those marks
 * carry real consequences: `inlineRunsToText` DROPS insertion-marked runs when it
 * projects a doc back to `tree.codoc` text, and rejecting a proposal deletes the
 * marked span outright.
 *
 * ProseMirror resolves stored marks from the enclosing text node, so a caret
 * placed INSIDE an agent's proposed span types text that inherits the agent's
 * mark (the marks are `inclusive: false`, which only protects the boundaries).
 * The user's own sentence then never reaches `tree.codoc`, never counts as a
 * change, and dies on the next reject — silently, with no way to notice.
 *
 * The invariant this plugin holds: **a span produced by user input carries no
 * engine mark.** Typing inside a proposal splits it instead of joining it, which
 * is what the reader means: the agent proposed those words, the human proposed
 * these. Attribution is decided by whose steps produced the text, not by which
 * neighborhood it landed in.
 *
 * System transactions (a projection load, an authorship stamp, our own cleanup)
 * are excluded — they are how agent marks legitimately ENTER the document.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { MARK_HYGIENE_META, isUserInput } from './edit-origin';
import { clampRanges, insertedRanges } from './tx-ranges';

/** Marks that encode "the agent proposed this" and must never cover user input. */
export const ENGINE_MARK_NAMES = ['insertion', 'deletion', 'formatChange'] as const;

const markHygieneKey = new PluginKey('codocMarkHygiene');

/** The plugin on its own, so tests can drive it against a real EditorState. */
export function markHygienePlugin(): Plugin {
    return new Plugin({
        key: markHygieneKey,
        appendTransaction: (transactions, _oldState, newState) => {
            if (!transactions.some(tr => tr.docChanged && isUserInput(tr))) return null;

            const types = ENGINE_MARK_NAMES
                .map(name => newState.schema.marks[name])
                .filter(Boolean);
            if (!types.length) return null;

            const ranges = clampRanges(
                insertedRanges(transactions, isUserInput),
                newState.doc.content.size,
            );
            if (!ranges.length) return null;

            const tr = newState.tr.setMeta(MARK_HYGIENE_META, true).setMeta('addToHistory', false);
            for (const [from, to] of ranges) {
                for (const type of types) {
                    if (newState.doc.rangeHasMark(from, to, type)) tr.removeMark(from, to, type);
                }
            }
            return tr.steps.length ? tr : null;
        },
    });
}

export const MarkHygiene = Extension.create({
    name: 'markHygiene',
    addProseMirrorPlugins() {
        return [markHygienePlugin()];
    },
});
