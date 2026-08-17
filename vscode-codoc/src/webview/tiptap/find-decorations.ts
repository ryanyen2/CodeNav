/**
 * find-decorations.ts — highlights for in-document find (⌘F), and the projection
 * of the tree doc into searchable blocks.
 *
 * Two decorations only: every hit gets `.ce-find-match`, and the one the reader
 * is on gets `.ce-find-current` as well. Both are decorations, never marks — a
 * mark would serialize into `tree.doc.json` and turn a search into an edit.
 *
 * The search surface is what an author can actually change: feature titles and
 * description paragraphs. Hidden ids and the `codoc:` targets inside citation
 * chips are addresses, not prose, so they are not searched — matching a symbol
 * path would return hits nobody could act on and that replace must never touch.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import type { FindMatch, SearchBlock } from '../find';
import { paraDisplayText } from './display-text';

export const FIND_UPDATED = 'codocFindUpdated';
const findKey = new PluginKey('codocFind');

export interface FindDecorationsOptions {
    getMatches: () => FindMatch[];
    /** Index of the current match, or -1. */
    getCurrent: () => number;
}

/**
 * The doc as searchable blocks, in document order: each feature's heading, then
 * its paragraphs. `base = pos + 1` and the text is display-space, so char offset
 * `i` maps to document position `base + i` — citation chips occupy exactly one
 * char each, which is what keeps a match after a chip anchored correctly.
 *
 * A paragraph before any heading (there should be none, but a torn projection
 * can produce one) is attributed to no feature rather than to the wrong one.
 */
export function searchBlocks(doc: PMModelNode): SearchBlock[] {
    const out: SearchBlock[] = [];
    let fid = '';
    doc.forEach((node, pos) => {
        if (node.type.name === 'featureHeading') {
            fid = (node.attrs.fid as string) || '';
            out.push({ text: paraDisplayText(node), base: pos + 1, fid, field: 'title' });
            return;
        }
        if (!node.isTextblock) return;
        out.push({ text: paraDisplayText(node), base: pos + 1, fid, field: 'description' });
    });
    return out;
}

function build(doc: PMModelNode, matches: FindMatch[], current: number): DecorationSet {
    if (!matches.length) return DecorationSet.empty;
    const size = doc.content.size;
    const decos: Decoration[] = [];
    for (let i = 0; i < matches.length; i++) {
        const m = matches[i];
        // A stale match list (the doc changed between search and paint) must not
        // throw out of a decoration build — clamp and drop anything out of range.
        if (m.from < 0 || m.to > size || m.to <= m.from) continue;
        decos.push(Decoration.inline(m.from, m.to, {
            class: i === current ? 'ce-find-match ce-find-current' : 'ce-find-match',
        }));
    }
    return DecorationSet.create(doc, decos);
}

export const FindDecorations = Extension.create<FindDecorationsOptions>({
    name: 'findDecorations',

    addOptions() {
        return { getMatches: () => [], getCurrent: () => -1 };
    },

    addProseMirrorPlugins() {
        const opts = (): FindDecorationsOptions => this.options;
        return [
            new Plugin({
                key: findKey,
                state: {
                    init: (_c, state) => build(state.doc, opts().getMatches(), opts().getCurrent()),
                    apply: (tr, old, _o, newState) => {
                        // On a doc change the owner re-runs the search and dispatches
                        // FIND_UPDATED; until then, mapping keeps the existing
                        // highlights under the caret instead of blinking them out.
                        if (tr.getMeta(FIND_UPDATED)) {
                            return build(newState.doc, opts().getMatches(), opts().getCurrent());
                        }
                        return old.map(tr.mapping, tr.doc);
                    },
                },
                props: { decorations(state) { return findKey.getState(state); } },
            }),
        ];
    },
});
