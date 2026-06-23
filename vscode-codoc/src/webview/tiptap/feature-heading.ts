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
import { newLocalId } from './local-id';

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
});
