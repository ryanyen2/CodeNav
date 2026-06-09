/**
 * author-mark.ts — the `author` mark (U1, webview-only). Per-character provenance.
 *
 * `mode` (pen|pencil) drives OPACITY, `role` drives COLOR/tint — the two are kept
 * separate so "who" and "how committed" read independently (U6 styles them). The
 * mark is `inclusive` so typing extends the active author's span. It persists in
 * `tree.doc.json` and is the schema basis for the Phase-3 role-based merge (a
 * reflected change on a `pen` span is demoted to a suggestion).
 */
import { Mark, mergeAttributes } from '@tiptap/core';

/** Sanitize a free-form role into a CSS-class-safe fragment. */
export function roleClass(role: string): string {
    return (role || 'unknown').toLowerCase().replace(/[^a-z0-9]+/g, '-');
}

export interface AuthorMarkOptions {
    HTMLAttributes: Record<string, unknown>;
}

export const AuthorMark = Mark.create<AuthorMarkOptions>({
    name: 'author',
    inclusive: true,
    // Two spans by different authors must NOT merge into one.
    excludes: '',

    addOptions() {
        return { HTMLAttributes: {} };
    },

    addAttributes() {
        return {
            authorId: { default: 'unknown' },
            role: { default: 'human' },
            mode: { default: 'pen' },
            ts: { default: 0 },
        };
    },

    parseHTML() {
        return [{ tag: 'span[data-author]' }];
    },

    renderHTML({ mark, HTMLAttributes }) {
        const role = (mark.attrs.role as string) || 'human';
        const mode = (mark.attrs.mode as string) || 'pen';
        return [
            'span',
            mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, {
                'data-author': '',
                'data-role': role,
                'data-mode': mode,
                class: `codoc-author codoc-mode-${mode} codoc-role-${roleClass(role)}`,
            }),
            0,
        ];
    },
});
