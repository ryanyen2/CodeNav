/**
 * feature-heading.ts — the `featureHeading` block node (U1, webview-only).
 *
 * The rich analogue of a `- Title  ⟨f-id⟩` line. Carries the hidden `fid` plus the
 * outliner `level` (= tree depth) and the `retired`/`realized` lifecycle bits as
 * node attributes — matching `pm-doc.FeatureHeadingAttrs` exactly so the pure
 * serializer can project it to `tree.codoc`. Rendered as a `div[data-feature-heading]`
 * styled by CSS per level (a custom outliner, not h1–h6 semantics).
 */
import { Node, mergeAttributes } from '@tiptap/core';

export interface FeatureHeadingOptions {
    HTMLAttributes: Record<string, unknown>;
}

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
