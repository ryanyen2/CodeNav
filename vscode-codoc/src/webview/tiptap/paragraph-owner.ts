/**
 * paragraph-owner.ts — identity-anchored prose (invariant I2, webview-only).
 *
 * A description paragraph belongs to a feature by IDENTITY, not by "the nearest heading
 * above it right now". Positional attribution is fragile under char-by-char editing:
 * type prose under feature A, then insert a heading B above it, and the prose silently
 * becomes B's description (taxonomy class B). The fix is an `ownerId` attr on each
 * paragraph — the fid|localId of its feature — that the settle diff reads instead of
 * geometry (see state/commands-from-doc.featureUnits).
 *
 * Two halves keep it honest:
 *   • Projected prose is owned from the first render — the Python `build_doc_from_store`
 *     seam stamps `ownerId = fid`, so a paragraph is anchored before any keystroke.
 *   • Brand-new prose (a fresh paragraph the user typed, ownerId still null) is
 *     CRYSTALLIZED to its positional owner by the appendTransaction below — within one
 *     transaction of its creation. ProseMirror copies block attrs across a split, so
 *     pressing Enter inside owned prose keeps the owner; the sweep only fills the nulls
 *     that other creation paths (a bare `paragraph.create()`, a paste) leave behind.
 *
 * By the time a heading is inserted above some prose, that prose already carries an
 * ownerId (either projected or crystallized on an earlier transaction), so it stays with
 * its real feature. Convergent like the sibling `uniqueLocalIdPlugin`: once every
 * paragraph is owned the next sweep finds no nulls and returns null — no transaction loop.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { NODE_PARAGRAPH, NODE_FEATURE_HEADING } from '../../state/pm-doc';
import { isUserInput } from './edit-origin';
import { insertedRanges, nodeArrived } from './tx-ranges';

const keepOwnerKey = new PluginKey('codocParagraphOwner');

/**
 * The keep-owner sweep as a ProseMirror plugin. After every doc-changing transaction it
 * walks the top-level blocks, tracking the nearest preceding heading's identity, and
 * stamps that identity onto any paragraph whose `ownerId` is still null. Prose before the
 * first heading has no owner and is left alone (it is unattributable, as it was before).
 */
export function keepParagraphOwnerPlugin() {
    return new Plugin({
        key: keepOwnerKey,
        appendTransaction(transactions, _oldState, newState) {
            if (!transactions.some(tr => tr.docChanged)) return null;
            // Spans this batch INSERTED, so a paragraph that arrived can be told from
            // one that merely stayed put. System transactions (a projection load) are
            // excluded — they insert the entire document, and every projected owner
            // would otherwise be re-derived from geometry on the spot.
            const arrivals = insertedRanges(transactions, isUserInput);
            const fixes: { pos: number; attrs: Record<string, unknown> }[] = [];
            let nearest: string | null = null;
            newState.doc.forEach((node, pos) => {
                if (node.type.name === NODE_FEATURE_HEADING) {
                    nearest = (node.attrs.fid as string | null) ?? (node.attrs.localId as string | null) ?? null;
                } else if (node.type.name === NODE_PARAGRAPH) {
                    const owner = (node.attrs.ownerId as string | null) ?? null;
                    if (!owner && nearest) {
                        fixes.push({ pos, attrs: { ...node.attrs, ownerId: nearest } });
                    } else if (owner && owner !== nearest && nearest
                               && nodeArrived(arrivals, pos, node.nodeSize)) {
                        // A paragraph copied out of one feature and pasted under another
                        // carries the old owner in its attrs, so the settle diff routed
                        // its text back to the feature it came from — the prose appeared
                        // under the new heading but was filed under the old one, with
                        // nothing on screen to suggest it. Arriving somewhere new means
                        // belonging there; only staying put preserves an owner (I2).
                        fixes.push({ pos, attrs: { ...node.attrs, ownerId: nearest } });
                    }
                }
            });
            if (!fixes.length) return null;
            const tr = newState.tr;
            for (const f of fixes) tr.setNodeMarkup(f.pos, undefined, f.attrs);
            tr.setMeta('addToHistory', false);  // a structural anchor, not a user undo step
            return tr;
        },
    });
}

/**
 * Extension that (a) adds the `ownerId` attribute to the `paragraph` node (a global
 * attribute so it composes with StarterKit's built-in Paragraph) and (b) installs the
 * keep-owner sweep. `ownerId` is a doc-only attr — like `localId`, it never leaks into
 * `tree.codoc` (the text projection reads content, not attrs).
 */
export const ParagraphOwner = Extension.create({
    name: 'paragraphOwner',

    addGlobalAttributes() {
        return [
            {
                types: [NODE_PARAGRAPH],
                attributes: {
                    ownerId: {
                        default: null,
                        parseHTML: el => (el as HTMLElement).getAttribute('data-owner-id') || null,
                        renderHTML: attrs => (attrs.ownerId ? { 'data-owner-id': attrs.ownerId } : {}),
                    },
                },
            },
        ];
    },

    addProseMirrorPlugins() {
        return [keepParagraphOwnerPlugin()];
    },
});
