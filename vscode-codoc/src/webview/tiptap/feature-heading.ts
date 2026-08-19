/**
 * feature-heading.ts — the `featureHeading` block node (U1, webview-only).
 *
 * The rich analogue of a `- Title  ⟨f-id⟩` line. Carries the hidden `fid` plus the
 * outliner `level` (= tree depth) and the `retired`/`realized` lifecycle bits as
 * node attributes — matching `pm-doc.FeatureHeadingAttrs` exactly so the pure
 * serializer can project it to `tree.codoc`. Rendered as a `div[data-feature-heading]`
 * styled by CSS per level (a custom outliner, not h1–h6 semantics).
 */
import { Node, mergeAttributes, textblockTypeInputRule } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { NodeType, Node as PMModelNode } from '@tiptap/pm/model';
import { newLocalId } from './local-id';
import { isUserInput } from './edit-origin';
import { insertedRanges, nodeArrived } from './tx-ranges';

export interface FeatureHeadingOptions {
    HTMLAttributes: Record<string, unknown>;
}

/** H1–H4 ⇄ feature level 0–3. `#` = top-level feature, `####` = depth-3. */
export const MAX_HEADING_LEVEL = 4;

export const FeatureHeading = Node.create<FeatureHeadingOptions>({
    name: 'featureHeading',
    group: 'block',
    content: 'inline*',
    defining: true,

    addOptions() {
        return { HTMLAttributes: {} };
    },

    addAttributes() {
        return {
            fid: {
                default: null,
                parseHTML: el => (el as HTMLElement).getAttribute('data-fid') || null,
                renderHTML: attrs => (attrs.fid ? { 'data-fid': attrs.fid } : {}),
            },
            // Stable client-side identity (KTD8) — minted at creation, survives the
            // pre-mint window + moves/type-changes/undo so decorations and gestures
            // never confuse one node for another. Coexists with `fid` (server identity).
            localId: {
                default: null,
                parseHTML: el => (el as HTMLElement).getAttribute('data-local-id') || null,
                renderHTML: attrs => (attrs.localId ? { 'data-local-id': attrs.localId } : {}),
            },
            level: {
                default: 0,
                parseHTML: el => Number((el as HTMLElement).getAttribute('data-level')) || 0,
                renderHTML: attrs => ({ 'data-level': String(attrs.level ?? 0) }),
            },
            retired: {
                default: false,
                parseHTML: el => (el as HTMLElement).getAttribute('data-retired') === 'true',
                renderHTML: attrs => (attrs.retired ? { 'data-retired': 'true' } : {}),
            },
            realized: {
                default: true,
                parseHTML: el => (el as HTMLElement).getAttribute('data-realized') !== 'false',
                renderHTML: attrs => (attrs.realized === false ? { 'data-realized': 'false' } : {}),
            },
            // A PLANNED node: an agent's proposed ADD, materialized in place (see
            // FeatureHeadingAttrs.proposed). Carries the proposal's event id, so the
            // verdict affordance on the heading knows what it is accepting and the
            // command path knows to leave the node alone.
            proposed: {
                default: null,
                parseHTML: el => (el as HTMLElement).getAttribute('data-proposed') || null,
                renderHTML: attrs => (attrs.proposed ? { 'data-proposed': String(attrs.proposed) } : {}),
            },
        };
    },

    addInputRules() {
        // Markdown `#`..`####` + space at the start of a block → a feature heading at
        // level 0..3 (H1–H4). The proven block-start conversion; a fresh localId (KTD8)
        // gives the new node stable identity immediately. (Splitting a new heading from
        // the END of a populated paragraph — the `headingFromInputRule` transform in
        // structure-commands.ts, unit-tested — is deferred until it can be verified in a
        // live editor; for now, press Enter for a new line, then type `## `.)
        return Array.from({ length: MAX_HEADING_LEVEL }, (_unused, idx) => {
            const hashes = idx + 1;
            return textblockTypeInputRule({
                find: new RegExp(`^#{${hashes}}\\s$`),
                type: this.type,
                getAttributes: () => ({
                    fid: null, level: hashes - 1, retired: false, realized: true,
                    localId: newLocalId(),
                }),
            });
        });
    },

    parseHTML() {
        return [{ tag: 'div[data-feature-heading]' }];
    },

    renderHTML({ HTMLAttributes }) {
        return [
            'div',
            mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, {
                'data-feature-heading': '',
                class: 'codoc-feature-heading',
            }),
            0,
        ];
    },

    addProseMirrorPlugins() {
        const type = this.type;
        return [uniqueLocalIdPlugin(type)];
    },
});

const uniqueLocalIdKey = new PluginKey('codocUniqueLocalId');

/**
 * Enforce the invariant Step 3's Python `local_id` keying depends on: a `localId` is
 * UNIQUE among the live feature headings. Copy-paste of a subtree and a heading split
 * both clone node attrs — so two live headings can momentarily share one `localId`,
 * and the local_id-keyed diff would then emit two AMEND/MOVE ops for one feature
 * (clobbering one, vanishing the paste). After every doc-changing transaction this
 * appendTransaction scans the headings and re-mints the localId (and clears `fid`, since
 * a clone is a NEW node, not the original) on the duplicate. Convergent: once ids are
 * unique the next run finds no duplicate and returns null, so there is no loop.
 *
 * WHICH occurrence is the clone matters, and document order does not answer it. Paste a
 * copied heading ABOVE its original and the copy comes first, so keeping "the first"
 * handed the copy the original's identity and re-minted the original as a brand-new
 * feature — its history, bindings and code attribution silently transferred to a
 * duplicate the user had just made. The honest signal is which node ARRIVED in this
 * transaction: a node that was already in the document is the original, wherever it now
 * sits. Document order is only the tiebreak when the batch inserted both (or neither),
 * as when a whole subtree is pasted at once.
 */
export function uniqueLocalIdPlugin(headingType: NodeType) {
    return new Plugin({
        key: uniqueLocalIdKey,
        appendTransaction(transactions, _oldState, newState) {
            if (!transactions.some(tr => tr.docChanged)) return null;
            const arrivals = insertedRanges(transactions, isUserInput);
            // Group the headings by localId first: deciding which of a colliding pair is
            // the newcomer needs to see both, which a single forward pass cannot.
            const byLocalId = new Map<string, Array<{ pos: number; node: PMModelNode }>>();
            newState.doc.forEach((node, pos) => {
                if (node.type !== headingType) return;
                const lid = node.attrs.localId as string | null;
                if (!lid) return;            // no id yet — nothing to dedup
                const group = byLocalId.get(lid);
                if (group) group.push({ pos, node });
                else byLocalId.set(lid, [{ pos, node }]);
            });

            const fixes: { pos: number; attrs: Record<string, unknown> }[] = [];
            for (const group of byLocalId.values()) {
                if (group.length < 2) continue;
                const stayed = group.filter(g => !nodeArrived(arrivals, g.pos, g.node.nodeSize));
                // The one node that was already here keeps the identity; if that cannot
                // distinguish them, fall back to document order as before.
                const keep = stayed.length === 1 ? stayed[0] : group[0];
                for (const g of group) {
                    if (g === keep) continue;
                    // A clone: give it its own identity, and drop any inherited fid so the
                    // daemon mints a fresh feature instead of binding it to the original.
                    fixes.push({ pos: g.pos, attrs: { ...g.node.attrs, localId: newLocalId(), fid: null } });
                }
            }
            if (!fixes.length) return null;
            const tr = newState.tr;
            for (const f of fixes) tr.setNodeMarkup(f.pos, undefined, f.attrs);
            tr.setMeta('addToHistory', false);  // a structural repair, not a user undo step
            return tr;
        },
    });
}
