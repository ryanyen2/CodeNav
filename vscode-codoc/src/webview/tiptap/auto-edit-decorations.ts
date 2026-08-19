/**
 * auto-edit-decorations.ts — the VERDICT on an unasked rewrite. Just the verdict.
 *
 * Loop A applies safe ops without asking, and exactly one of them changes what the
 * document SAYS: a small AMEND rewrites a description in place. Nobody is prompted, so
 * unless the author happens to reread that paragraph they never learn it moved.
 *
 * This module used to answer that with a whole review surface of its own — a margin
 * rail, an underline on the added words, a strikethrough ghost of the displaced ones,
 * and the Keep / Restore pair. The diff half has moved to `state/settlement.ts`, whose
 * CODE channel is exactly this fact ("the codebase already says this") drawn in the
 * grammar every other channel shares. What is left here is the part that was always
 * this module's own: the decision.
 *
 * TWO WORKAROUNDS WENT WITH THE DIFF, and it is worth saying why they are not missed,
 * because both were compensating for the same structural flaw:
 *
 *   • `locallyEdited` — the layer stood down entirely on a feature the reader had since
 *     edited, because its diff ran "what it said before" against "what is on screen",
 *     and once the author typed there, their words sat inside `current` and the
 *     underline claimed the loop had written them.
 *   • `arrivedAs` — a module-level memo of what each rewrite looked like on its FIRST
 *     render, because `locallyEdited` is fed from the draft and held sets, which only
 *     arrive after a settle; between the first keystroke and that settle the surface
 *     was drawing the author's own sentence back at them as somebody else's.
 *
 * Both existed because the diff had no way to tell the loop's words from the author's
 * once they shared a paragraph. The settlement model does: code claims are computed
 * against the projection and carried forward through the author's own diff, and a claim
 * whose sentence the author has edited is void by construction. So the stale-baseline
 * problem is answered where it arises instead of being fenced off with two heuristics
 * that had to agree.
 *
 * The verdict keeps its own asymmetry, which is real and is stated in the buttons: this
 * rewrite happened because the CODE already changed. Keep costs nothing — the document
 * already says this. Restore is a real edit; it re-asserts a claim the code may no
 * longer honor, so it goes back through the authored-command channel where the daemon
 * classifies it and, if it implies code work, holds it for hand-off like any other
 * code-implying edit.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import { nextDecorations } from './decoration-policy';
import { alignParas, mdDisplayText, paraDisplayText, ATOM_CHAR } from './display-text';
import { sentenceDiff } from '../../state/doc-diff';
import { displacedHuman } from '../../state/auto-edits';
import type { AutoEdit } from '../../state/bindings-model';

export const AUTO_EDITS_UPDATED = 'codocAutoEditsUpdated';
const autoKey = new PluginKey('codocAutoEditDecorations');

export interface AutoEditHandlers {
    /** Keep codoc's wording — acknowledges the rewrite; the mark clears for good. */
    keep: (fid: string, at: string) => void;
    /** Restore the previous wording — an ordinary authored edit; the daemon
     *  classifies it and may queue reconcile work (held until hand-off). */
    revert: (fid: string, at: string, prev: string) => void;
}

export interface AutoEditDecorationsOptions {
    /** fid → the rewrite the reader has NOT resolved yet (already filtered by the
     *  seen-set; this layer draws whatever it is handed). */
    getUnseen: () => Record<string, AutoEdit>;
    /** Features the reader has since edited themselves — this surface stands down on
     *  them (see `buildAutoEditDecorations`). */
    getLocallyEdited?: () => Set<string>;
    handlers?: AutoEditHandlers;
}


/** The verdict strip: origin in plain words, the loop's own recorded WHY, and the
 *  Keep / Restore pair with their consequences spelled out. Mirrors the shape of
 *  suggestion-decorations' verdictStrip so the two review surfaces read as one. */
function verdictStrip(
    fid: string, edit: AutoEdit, handlers: AutoEditHandlers | undefined,
): HTMLElement {
    const mine = displacedHuman(edit);
    const row = document.createElement('span');
    row.className = 'ce-verdict autoedit' + (mine ? ' mine' : '');
    row.contentEditable = 'false';

    const dir = document.createElement('span');
    dir.className = 'ce-tc-dir';
    dir.textContent = mine ? 'rewrote your wording · from code' : 'rewritten · from code';
    dir.title = (mine
        ? 'codoc edited words YOU wrote, to match code that already changed. '
        : 'codoc rewrote this description to match code that already changed. ')
        + (edit.rationale ? `Why: ${edit.rationale}` : '');
    row.append(dir);

    if (!handlers) return row;
    const actions = document.createElement('span');
    actions.className = 'ce-tc-btns';
    const once = (fn: () => void) => (ev: Event): void => {
        ev.preventDefault();
        ev.stopPropagation();
        actions.querySelectorAll('button').forEach(b => { (b as HTMLButtonElement).disabled = true; });
        actions.classList.add('applying');
        fn();
    };
    const restore = document.createElement('button');
    restore.type = 'button';
    restore.className = 'ce-diff-btn reject';
    restore.textContent = 'Restore mine';
    restore.title = 'Put the previous wording back. The code keeps its changes — '
        + 'restoring a claim the code no longer matches is a real edit, so it goes '
        + 'to the agent for reconciliation (held until you send it).';
    restore.addEventListener('mousedown', ev => ev.preventDefault());
    restore.addEventListener('click', once(() => handlers.revert(fid, edit.at, edit.prev)));
    const keep = document.createElement('button');
    keep.type = 'button';
    keep.className = 'ce-diff-btn accept';
    keep.textContent = 'Keep';
    keep.title = 'Keep codoc\'s wording. The doc already says this — nothing else changes.';
    keep.addEventListener('mousedown', ev => ev.preventDefault());
    keep.addEventListener('click', once(() => handlers.keep(fid, edit.at)));
    actions.append(restore, keep);
    row.append(actions);
    return row;
}

/** Build the marks for every unresolved rewrite present in this doc. Exported
 *  headless so the anchoring can be tested without a view (the widget factories
 *  only run on render). */
export function buildAutoEditDecorations(
    doc: PMModelNode, unseen: Record<string, AutoEdit>,
    handlers?: AutoEditHandlers,
    locallyEdited?: Set<string>,
): DecorationSet {
    if (!Object.keys(unseen).length) return DecorationSet.empty;
    const decos: Decoration[] = [];
    interface Group { fid: string; headNode: PMModelNode; headPos: number }
    const groups: Group[] = [];
    let g: Group | null = null;
    doc.forEach((node, pos) => {
        if (node.type.name === 'featureHeading') {
            const fid = node.attrs.fid as string | null;
            // Stand down on a feature the reader has since rewritten themselves.
            //
            // Still correctness, and still this module's own concern rather than the
            // diff's: "Restore mine" re-authors the wording the loop displaced, and once
            // the author has edited the same feature that wording is two revisions stale
            // — restoring it would discard their newer text. The verdict is on words
            // that are no longer there.
            const owed = !!fid && !!unseen[fid] && !locallyEdited?.has(fid);
            g = owed && fid ? { fid, headNode: node, headPos: pos } : null;
            if (g) groups.push(g);
        }
    });

    for (const grp of groups) {
        const edit = unseen[grp.fid];
        // ONE resolution surface per feature, on the feature — the same rule every
        // other proposal follows (suggestion-decorations rule 2). The DIFF it is a
        // verdict on is drawn by the settlement layer's code channel; this is only
        // the decision.
        decos.push(Decoration.widget(grp.headPos + 1 + grp.headNode.content.size,
            () => verdictStrip(grp.fid, edit, handlers),
            { side: 1, key: 'auto-v-' + grp.fid + '@' + edit.at }));
    }
    return DecorationSet.create(doc, decos);
}

export const AutoEditDecorations = Extension.create<AutoEditDecorationsOptions>({
    name: 'autoEditDecorations',

    addOptions() {
        return { getUnseen: () => ({}), handlers: undefined };
    },

    addProseMirrorPlugins() {
        const getUnseen = (): Record<string, AutoEdit> => this.options.getUnseen();
        const handlers = (): AutoEditHandlers | undefined => this.options.handlers;
        const locallyEdited = (): Set<string> => this.options.getLocallyEdited?.() ?? new Set();
        return [
            new Plugin({
                key: autoKey,
                state: {
                    init: (_c, state) => buildAutoEditDecorations(
                        state.doc, getUnseen(), handlers(), locallyEdited()),
                    // Structure-keyed (decoration-policy): the rewrite is a fact from the
                    // payload, not something the reader's typing changes. Typing inside a
                    // marked paragraph only MOVES the marks — and the reader editing the
                    // prose themselves is exactly when re-diffing it against the loop's
                    // old text would start underlining their own words back at them.
                    apply: (tr, old, _o, newState) => nextDecorations(
                        tr, old, !!tr.getMeta(AUTO_EDITS_UPDATED),
                        () => buildAutoEditDecorations(
                            newState.doc, getUnseen(), handlers(), locallyEdited()),
                    ),
                },
                props: { decorations(state) { return autoKey.getState(state); } },
            }),
        ];
    },
});
