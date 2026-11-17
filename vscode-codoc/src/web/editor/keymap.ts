/**
 * keymap.ts — which key runs which gesture (09-editor §4).
 *
 * Enter, Backspace, Delete, Tab and the `## ` input rule change the document's
 * structure, so each is bound to a command that emits its own ops
 * (commands.ts). Where no command applies, the key falls through to
 * ProseMirror's base keymap, whose text edits the recorder reads from the
 * transaction and whose structural edits the guard refuses.
 *
 * Two refusals are decided here. A deletion that would join a heading with
 * prose moves the caret across the boundary instead. A selection across blocks
 * that cannot be deleted as ops is left alone, and the person is told why.
 *
 * Undo and redo are bound to nothing: ProseMirror's history is not installed,
 * and the page's own handler submits recorded inverses (app.ts).
 */
import { baseKeymap } from '@tiptap/pm/commands';
import { InputRule, inputRules } from '@tiptap/pm/inputrules';
import { keymap } from '@tiptap/pm/keymap';
import { TextSelection, type EditorState, type Plugin } from '@tiptap/pm/state';
import type { EditorView } from '@tiptap/pm/view';

import { deleteSelection, headingFromRule, mergeBackward, mergeForward, relevel, selectedBlocks, splitBlock, type Env, type Gesture } from './commands';

/** The key plugins, in the order they must be installed. `refuse` is told why a gesture was not performed. */
export function gestureKeys(env: Env, refuse: (why: string) => void): Plugin[] {
    const run = (view: EditorView, g: Gesture | null): boolean => {
        if (!g) return false;
        view.dispatch(g.tr);
        return true;
    };

    /** Delete a selection that spans blocks, as ops. False when the selection
     *  is empty or inside one block, which the recorder handles. */
    const cutSelection = (state: EditorState, view: EditorView): boolean | 'refused' => {
        if (state.selection.empty) return false;
        const parts = selectedBlocks(state);
        if (parts.length < 2) return false;
        const g = deleteSelection(state, env);
        if (!g) {
            refuse(parts.some(p => p.node.type.name === 'heading')
                ? 'a selection across a heading is not a delete; use Delete feature'
                : 'a selection across a block that is not prose is not a delete');
            return 'refused';
        }
        view.dispatch(g.tr);
        return true;
    };

    /** At a boundary with a heading, move the caret across it instead of merging. */
    const refuseHeadingMerge = (state: EditorState, view: EditorView, dir: 'back' | 'forward'): boolean => {
        const { $from, empty } = state.selection;
        if (!empty || $from.depth !== 1) return false;
        if (dir === 'back' && $from.parentOffset === 0 && $from.index(0) > 0) {
            const prev = state.doc.child($from.index(0) - 1);
            if (prev.type.name === 'heading' || $from.parent.type.name === 'heading') {
                view.dispatch(state.tr.setSelection(TextSelection.create(state.doc, $from.before(1) - 1)));
                return true;
            }
        }
        if (dir === 'forward' && $from.parentOffset === $from.parent.content.size && $from.index(0) + 1 < state.doc.childCount) {
            const next = state.doc.child($from.index(0) + 1);
            if (next.type.name === 'heading' || $from.parent.type.name === 'heading') {
                view.dispatch(state.tr.setSelection(TextSelection.create(state.doc, $from.after(1) + 1)));
                return true;
            }
        }
        return false;
    };

    const keys = keymap({
        Enter: (state, _dispatch, view) => {
            const cut = cutSelection(state, view!);
            if (cut === 'refused') return true;
            if (cut) return run(view!, splitBlock(view!.state, env)) || true;
            if (state.selection.empty) return run(view!, splitBlock(state, env));
            // One block with something selected. The delete is ordinary text
            // the recorder reads, and the split is a gesture: two dispatches in
            // that order, because the guard would refuse them as one.
            view!.dispatch(state.tr.deleteSelection());
            return run(view!, splitBlock(view!.state, env)) || true;
        },
        Backspace: (state, _dispatch, view) => {
            if (cutSelection(state, view!)) return true;
            return run(view!, mergeBackward(state, env)) || refuseHeadingMerge(state, view!, 'back');
        },
        Delete: (state, _dispatch, view) => {
            if (cutSelection(state, view!)) return true;
            return run(view!, mergeForward(state, env)) || refuseHeadingMerge(state, view!, 'forward');
        },
        Tab: (state, _dispatch, view) => run(view!, relevel(state, env, 1)),
        'Shift-Tab': (state, _dispatch, view) => run(view!, relevel(state, env, -1)),
        'Mod-z': () => true,
        'Mod-Shift-z': () => true,
        'Mod-y': () => true,
    });

    const headingRule = new InputRule(/^(#{1,6})\s$/, (state, match) => headingFromRule(state, env, match)?.tr ?? null);

    return [keys, inputRules({ rules: [headingRule] }), keymap(baseKeymap)];
}
