/**
 * code-ref.ts — the `codeRef` inline atom (U1, webview-only).
 *
 * A single, non-editable citation chip standing in for `[label](codoc:file#symbol)`.
 * Attrs match `pm-doc.CodeRefAttrs` (raw label kept verbatim) so the serializer
 * projects it back to the exact markdown. Clicking it posts `open-binding` to the
 * host (wired in U5/U3); here it is just the schema + chip rendering.
 */
import { Node, mergeAttributes } from '@tiptap/core';

export interface CodeRefOptions {
    HTMLAttributes: Record<string, unknown>;
}

export const CodeRef = Node.create<CodeRefOptions>({
    name: 'codeRef',
    group: 'inline',
    inline: true,
    atom: true,
    selectable: true,

    addOptions() {
        return { HTMLAttributes: {} };
    },

    addAttributes() {
        return {
            label: { default: '' },
            file: { default: '' },
            symbol: { default: null },
        };
    },

    parseHTML() {
        return [{ tag: 'span[data-code-ref]' }];
    },

    renderHTML({ node, HTMLAttributes }) {
        const label = (node.attrs.label as string) || '';
        const file = (node.attrs.file as string) || '';
        const symbol = (node.attrs.symbol as string | null) ?? null;
        const text = label || symbol || file;
        return [
            'span',
            mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, {
                'data-code-ref': '',
                'data-file': file,
                'data-symbol': symbol ?? '',
                class: 'codoc-code-ref',
                title: symbol ? `codoc:${file}#${symbol}` : `codoc:${file}`,
                // Keyboard-reachable so the hover card (U4) opens on Enter/Space. A
                // render attr only — never serialized (doc-serialize.ts owns markdown).
                tabindex: '0',
            }),
            text,
        ];
    },
});
