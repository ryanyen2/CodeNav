/**
 * keymap.ts: which key runs which gesture.
 *
 * Enter, Backspace, Delete, Tab and the `## `, `- `, `1. ` input rules change
 * the document's structure, so each is bound to a command that emits its own
 * ops (commands.ts). Where no command applies, the key falls through to
 * ProseMirror's base keymap, whose text edits the recorder reads from the
 * transaction and whose structural edits the guard refuses.
 *
 * Enter and Shift-Enter do the same thing: a heading or prose block holds one
 * line, so a line break is a new block. In a source block both type a newline,
 * and Mod-Enter leaves it for a new paragraph after it.
 *
 * Undo and redo are bound to nothing: ProseMirror's history is not installed,
 * and the page's own handler submits recorded inverses (app.ts).
 */
import { baseKeymap, toggleMark } from '@tiptap/pm/commands';
import { InputRule, inputRules } from '@tiptap/pm/inputrules';
import { keymap } from '@tiptap/pm/keymap';
import { Selection, type Command, type EditorState, type Plugin } from '@tiptap/pm/state';
import type { EditorView } from '@tiptap/pm/view';

import { ITEMS, SOURCE, kindOf } from '../calc/projection';
import { deleteSelection, indent, mergeBackward, mergeForward, paragraphAfter, removeEmpty, selectedBlocks, setKind,
         sourceFromMarker, splitBlock, type Env, type Gesture } from './commands';

/** The key plugins, in the order they must be installed. `openLink` opens the link field over the selection. */
export function gestureKeys(env: Env, openLink: () => boolean): Plugin[] {
    const run = (view: EditorView, g: Gesture | null): boolean => {
        if (!g) return false;
        view.dispatch(g.tr);
        return true;
    };
    const caretKind = (state: EditorState): string | null =>
        (state.selection.$from.depth === 1 ? kindOf(state.selection.$from.parent) : null);

    /** A selection across blocks is deleted as ops, or refused with a notice.
     *  'none' when the selection is empty or inside one block. `landing`: a new
     *  line follows, so the caret stays inside what was selected. */
    const cutAcross = (state: EditorState, view: EditorView, landing = false): 'none' | 'done' | 'refused' => {
        if (state.selection.empty || selectedBlocks(state).length < 2) return 'none';
        return run(view, deleteSelection(state, env, landing)) ? 'done' : 'refused';
    };

    /** At a block boundary that cannot merge, move the caret across it instead. */
    const stepAcross = (state: EditorState, view: EditorView, dir: 'back' | 'forward'): boolean => {
        const { $from, empty } = state.selection;
        if (!empty || $from.depth !== 1) return false;
        if (dir === 'back' && $from.parentOffset === 0 && $from.index(0) > 0) {
            view.dispatch(state.tr.setSelection(Selection.near(state.doc.resolve($from.before(1) - 1), -1)));
            return true;
        }
        if (dir === 'forward' && $from.parentOffset === $from.parent.content.size && $from.index(0) + 1 < state.doc.childCount) {
            view.dispatch(state.tr.setSelection(Selection.near(state.doc.resolve($from.after(1) + 1), 1)));
            return true;
        }
        return false;
    };

    const enter: Command = (state, _dispatch, view) => {
        const selected = !state.selection.empty;
        if (cutAcross(state, view!, true) === 'refused') return true;
        state = view!.state;
        if (SOURCE.has(caretKind(state) ?? '')) { view!.dispatch(state.tr.insertText('\n')); return true; }
        if (!state.selection.empty) {
            // One block with something selected. The delete is ordinary text
            // the recorder reads, and the split is a gesture: two dispatches in
            // that order, because the guard would refuse them as one.
            view!.dispatch(state.tr.deleteSelection());
            state = view!.state;
        }
        // Enter over a selection splits where the selection was. The empty item
        // and marker rules are for Enter at a caret.
        if (selected) return run(view!, splitBlock(state, env)) || true;
        const item = ITEMS.has(caretKind(state) ?? '') && state.selection.$from.parent.content.size === 0;
        return run(view!, item ? setKind(state, env, 'paragraph') : sourceFromMarker(state, env) ?? splitBlock(state, env)) || true;
    };

    const keys = keymap({
        Enter: enter,
        'Shift-Enter': enter,
        'Mod-Enter': (state, _dispatch, view) => run(view!, paragraphAfter(state, env)),
        Backspace: (state, _dispatch, view) => {
            if (cutAcross(state, view!) !== 'none') return true;
            const { $from, empty } = state.selection;
            if (empty && $from.parentOffset === 0 && ITEMS.has(caretKind(state) ?? '')) {
                // An item steps out one level at a time, then becomes a paragraph.
                return run(view!, $from.parent.attrs.level > 1 ? indent(state, env, -1) : setKind(state, env, 'paragraph'));
            }
            if (empty && $from.parentOffset === 0 && caretKind(state) === 'heading') {
                // A plain heading becomes a paragraph, so a stray `# ` is undone in one key. One that
                // owns code, has a child heading, has a hand-off not yet realized, is named by an open
                // plan, or is in any phase but edited or synced is a feature to keep.
                const fid = $from.parent.attrs.bid as string, level = $from.parent.attrs.level as number;
                const proj = env.projection?.();
                let next: number | null = null;
                for (let i = $from.index(0) + 1; i < state.doc.childCount && next === null; i++) {
                    if (state.doc.child(i).type.name === 'heading') next = state.doc.child(i).attrs.level;
                }
                const w = proj?.watermarks?.[fid], phase = proj?.phases[fid];
                const keep = !!proj?.bindings.by_feature[fid]?.length || (next !== null && next > level)
                    || (phase !== undefined && phase !== 'edited' && phase !== 'synced')
                    || (!!w && Object.entries(w.handoff).some(([s, seq]) => (w.realized[s] ?? 0) < seq))
                    || !!proj?.plans.some(p => p.status === 'open' && (p.target === fid || p.handoff?.includes(fid)));
                if (!keep) return run(view!, setKind(state, env, 'paragraph'));
                env.refuse?.('Delete feature from the / menu');
                return true;
            }
            return run(view!, mergeBackward(state, env) ?? removeEmpty(state, env, 'back')) || stepAcross(state, view!, 'back');
        },
        Delete: (state, _dispatch, view) => {
            if (cutAcross(state, view!) !== 'none') return true;
            return run(view!, mergeForward(state, env) ?? removeEmpty(state, env, 'forward')) || stepAcross(state, view!, 'forward');
        },
        // Tab never leaves the editor: where nothing indents, it does nothing.
        Tab: (state, _dispatch, view) => { run(view!, indent(state, env, 1)); return true; },
        'Shift-Tab': (state, _dispatch, view) => { run(view!, indent(state, env, -1)); return true; },
        'Mod-b': (state, dispatch) => toggleMark(state.schema.marks.bold)(state, dispatch),
        'Mod-e': (state, dispatch) => toggleMark(state.schema.marks.code)(state, dispatch),
        'Mod-k': () => openLink(),
        'Mod-z': () => true,
        'Mod-Shift-z': () => true,
        'Mod-y': () => true,
    });

    /** An input rule that turns a paragraph (or, with `any`, a heading or a
     *  prose block) into `kind`, dropping the typed marker. */
    const rule = (re: RegExp, kind: string, level: (m: RegExpMatchArray) => number | undefined, any = false) =>
        new InputRule(re, (state, match) => {
            if (!any && state.selection.$from.parent.type.name !== 'paragraph') return null;
            return setKind(state, env, kind, level(match), match[0].length - 1)?.tr ?? null;
        });

    return [keys, inputRules({ rules: [
        rule(/^(#{1,6})\s$/, 'heading', m => m[1].length, true),
        rule(/^[-*]\s$/, 'bullet', () => 1),
        rule(/^\d{1,9}\.\s$/, 'numbered', () => 1),
    ] }), keymap(baseKeymap)];
}
