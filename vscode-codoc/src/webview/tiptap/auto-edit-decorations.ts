/**
 * auto-edit-decorations.ts — "codoc rewrote this while you weren't looking."
 *
 * Loop A applies safe ops without asking, and exactly one of them changes what the
 * document SAYS: a small AMEND rewrites a description in place. Nobody is prompted, so
 * unless the author happens to reread that paragraph they never find out. Everything
 * else the loop does automatically is index machinery and is deliberately never shown
 * (the triage is in `render._auto_edits`).
 *
 * The mark is the SAME shape the reader's own uncommitted edit wears — a gutter rail
 * plus an underline on the words that moved — in the LOOP's ink rather than theirs.
 * That is the point: "something changed here and here is what" is one idea, and it
 * should not need a second visual language depending on who did it. The diff comes out
 * of the same display-space machinery captured-decorations uses (`blockDiffSpans`,
 * `alignParas`), so a codeRef chip or an inserted paragraph cannot skew it.
 *
 * It is UNSEEN until the reader has actually been on that section — see
 * state/auto-edits.ts. There is no dismiss button: acknowledgement is reading, and a
 * button would only add a chore to a notification that already knows when it is done.
 * The dwell is longer when the loop displaced the reader's OWN words, so that case
 * survives a fast scroll down the page.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import { nextDecorations } from './decoration-policy';
import { alignParas, mdDisplayText, paraDisplayText } from './display-text';
import { blockDiffSpans } from './captured-decorations';
import { displacedHuman } from '../../state/auto-edits';
import type { AutoEdit } from '../../state/bindings-model';
import { icon } from '../icons';

export const AUTO_EDITS_UPDATED = 'codocAutoEditsUpdated';
const autoKey = new PluginKey('codocAutoEditDecorations');

export interface AutoEditDecorationsOptions {
    /** fid → the rewrite the reader has NOT caught up on yet (already filtered by the
     *  seen-set; this layer draws whatever it is handed). */
    getUnseen: () => Record<string, AutoEdit>;
}

/** The loop's prose is stored as one string with blank-line paragraph breaks — the
 *  same split `_description_lines` round-trips through. */
function prevParas(prev: string): string[] {
    return prev.split(/\n{2,}/).map(s => s.trim());
}

/** Build the marks for every unseen rewrite present in this doc. Exported headless so
 *  the anchoring can be tested without a view (the widget factory only runs on render). */
export function buildAutoEditDecorations(
    doc: PMModelNode, unseen: Record<string, AutoEdit>,
): DecorationSet {
    if (!Object.keys(unseen).length) return DecorationSet.empty;
    const decos: Decoration[] = [];
    interface Para { node: PMModelNode; pos: number }
    interface Group { fid: string; headNode: PMModelNode; headPos: number; paras: Para[] }
    const groups: Group[] = [];
    let g: Group | null = null;
    doc.forEach((node, pos) => {
        if (node.type.name === 'featureHeading') {
            const fid = node.attrs.fid as string | null;
            g = fid && unseen[fid] ? { fid, headNode: node, headPos: pos, paras: [] } : null;
            if (g) groups.push(g);
            return;
        }
        if (g && node.type.name === 'paragraph') g.paras.push({ node, pos });
    });

    for (const grp of groups) {
        const edit = unseen[grp.fid];
        const mine = displacedHuman(edit);
        const cls = 'ce-autoedit' + (mine ? ' mine' : '');
        decos.push(Decoration.node(grp.headPos, grp.headPos + grp.headNode.nodeSize,
                                   { class: 'ce-autoedit-head' }));
        // One quiet mark on the heading carrying the whole explanation, so the reader
        // can find out WHY without hunting: the loop's own recorded rationale.
        decos.push(Decoration.widget(grp.headPos + grp.headNode.nodeSize - 1, () => {
            const chip = document.createElement('span');
            chip.className = 'ce-autoedit-chip' + (mine ? ' mine' : '');
            chip.contentEditable = 'false';
            chip.title = (mine
                ? 'codoc edited your wording here to match the code. '
                : 'codoc rewrote this description to match the code. ')
                + (edit.rationale ? `Why: ${edit.rationale}. ` : '')
                + 'The underlined words are what changed. This clears once you have read it.';
            chip.append(icon('arrows-clockwise'));
            return chip;
        }, { side: 1, key: 'auto-' + grp.fid + '@' + edit.at }));

        const baseDisplay = prevParas(edit.prev).map(mdDisplayText);
        const curDisplay = grp.paras.map(p => paraDisplayText(p.node));
        const pairing = alignParas(baseDisplay, curDisplay);
        grp.paras.forEach((p, k) => {
            if (p.node.content.size === 0) return;
            decos.push(Decoration.node(p.pos, p.pos + p.node.nodeSize, { class: cls }));
            const bi = pairing[k];
            for (const sp of blockDiffSpans(bi == null ? '' : baseDisplay[bi], curDisplay[k], p.pos + 1)) {
                // Only the ADDED runs are underlined. A deletion caret would be a third
                // mark competing for the same glance, and the reader is being told about
                // a change they did not make — "what does it say now" is the question,
                // not "what character vanished". The full previous wording is one hover
                // away on the chip.
                if (sp.kind === 'add') decos.push(Decoration.inline(sp.from, sp.to, { class: 'ce-autoedit-add' }));
            }
        });
    }
    return DecorationSet.create(doc, decos);
}

export const AutoEditDecorations = Extension.create<AutoEditDecorationsOptions>({
    name: 'autoEditDecorations',

    addOptions() {
        return { getUnseen: () => ({}) };
    },

    addProseMirrorPlugins() {
        const getUnseen = (): Record<string, AutoEdit> => this.options.getUnseen();
        return [
            new Plugin({
                key: autoKey,
                state: {
                    init: (_c, state) => buildAutoEditDecorations(state.doc, getUnseen()),
                    // Structure-keyed (decoration-policy): the rewrite is a fact from the
                    // payload, not something the reader's typing changes. Typing inside a
                    // marked paragraph only MOVES the marks — and the reader editing the
                    // prose themselves is exactly when re-diffing it against the loop's
                    // old text would start underlining their own words back at them.
                    apply: (tr, old, _o, newState) => nextDecorations(
                        tr, old, !!tr.getMeta(AUTO_EDITS_UPDATED),
                        () => buildAutoEditDecorations(newState.doc, getUnseen()),
                    ),
                },
                props: { decorations(state) { return autoKey.getState(state); } },
            }),
        ];
    },
});
