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
 * and Mod-Enter leaves it for a new paragraph after it. In a code block Tab
 * and Shift-Tab indent lines, as typed text the recorder reads.
 *
 * Undo and redo are bound to nothing: ProseMirror's history is not installed,
 * and the page's own handler submits recorded inverses (app.ts).
 */
import { baseKeymap, toggleMark } from '@tiptap/pm/commands';
import { InputRule, inputRules } from '@tiptap/pm/inputrules';
import { keymap } from '@tiptap/pm/keymap';
import { Selection, type Command, type EditorState, type Plugin, type Transaction } from '@tiptap/pm/state';
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

    /** Tab never leaves the editor: where nothing indents, it does nothing. */
    const tab = (delta: 1 | -1): Command => (state, _dispatch, view) => {
        const code = indentCode(state, delta);
        if (code) view!.dispatch(code);
        else run(view!, indent(state, delta));
        return true;
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
                return run(view!, $from.parent.attrs.level > 1 ? indent(state, -1) : setKind(state, env, 'paragraph'));
            }
            if (empty && $from.parentOffset === 0 && caretKind(state) === 'heading') {
                // A plain heading becomes a paragraph, so a stray `# ` is undone in one key. One that
                // owns code, has a child heading, has work in flight (a hand-off not yet realized, a
                // plan offered for a verdict), or is in any phase but edited or synced is a feature to keep.
                const fid = $from.parent.attrs.bid as string, level = $from.parent.attrs.level as number;
                const proj = env.projection?.();
                let next: number | null = null;
                for (let i = $from.index(0) + 1; i < state.doc.childCount && next === null; i++) {
                    if (state.doc.child(i).type.name === 'heading') next = state.doc.child(i).attrs.level;
                }
                const phase = proj?.phases[fid];
                const keep = !!proj?.bindings.by_feature[fid]?.length || (next !== null && next > level)
                    || (phase !== undefined && phase !== 'edited' && phase !== 'synced')
                    || !!proj?.in_flight?.includes(fid);
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
        Tab: tab(1),
        'Shift-Tab': tab(-1),
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

/**
 * Tab or Shift-Tab in a code block: the block's indent unit (its smallest
 * leading run of spaces, else four) typed at the start of each line the
 * selection touches, or up to one unit removed from it. Ordinary text edits,
 * which the recorder reads. Null outside a code block or when nothing changes.
 */
function indentCode(state: EditorState, delta: 1 | -1): Transaction | null {
    const { $from, $to } = state.selection;
    if (!$from.sameParent($to) || kindOf($from.parent) !== 'code') return null;
    const lines = $from.parent.textContent.split('\n');
    const lead = (line: string): number => /^ */.exec(line)![0].length;
    const indented = lines.map(lead).filter(n => n > 0);
    const unit = indented.length ? Math.min(...indented) : 4;
    const f = $from.parentOffset, t = $to.parentOffset;
    let tr = state.tr;
    let at = 0;
    for (const line of lines) {
        // The line holding the selection's start, and each line that starts before its end.
        if (at + line.length >= f && (at <= f || at < t)) {
            const pos = tr.mapping.map($from.start() + at);
            if (delta > 0) tr = tr.insertText(' '.repeat(unit), pos);
            else if (lead(line)) tr = tr.delete(pos, pos + Math.min(unit, lead(line)));
        }
        at += line.length + 1;
    }
    return tr.docChanged ? tr : null;
}
