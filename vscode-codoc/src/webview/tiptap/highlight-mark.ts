/**
 * highlight-mark.ts — a minimal `highlight` mark (U7). Presentation-only; lives in
 * `tree.doc.json` and is dropped from the `tree.codoc` text projection. Written as
 * a custom mark (rather than pulling another dependency) since it carries no attrs.
 */
import { Mark, mergeAttributes } from '@tiptap/core';

export const HighlightMark = Mark.create({
    name: 'highlight',

    parseHTML() {
        return [{ tag: 'mark' }];
    },

    renderHTML({ HTMLAttributes }) {
        return ['mark', mergeAttributes(HTMLAttributes, { class: 'codoc-highlight' }), 0];
    },

    addCommands() {
        return {
            toggleHighlight:
                () =>
                ({ commands }) =>
                    commands.toggleMark(this.name),
        };
    },
});

declare module '@tiptap/core' {
    interface Commands<ReturnType> {
        highlight: {
            toggleHighlight: () => ReturnType;
        };
    }
}
