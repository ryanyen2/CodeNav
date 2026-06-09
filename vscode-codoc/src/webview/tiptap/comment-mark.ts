/**
 * comment-mark.ts — the `comment` mark (U7). Anchors a comment thread to a range.
 *
 * Phase 1 only STORES the anchor (`threadId`) and renders an affordance; the
 * "comment = ask-the-LLM-for-a-higher-level-edit" behavior is Phase 2. Lives in
 * `tree.doc.json`; dropped from the `tree.codoc` text projection.
 */
import { Mark, mergeAttributes } from '@tiptap/core';

export const CommentMark = Mark.create({
    name: 'comment',
    inclusive: false,

    addAttributes() {
        return {
            threadId: {
                default: null,
                parseHTML: el => (el as HTMLElement).getAttribute('data-thread-id'),
                renderHTML: attrs => (attrs.threadId ? { 'data-thread-id': attrs.threadId } : {}),
            },
        };
    },

    parseHTML() {
        return [{ tag: 'span[data-comment]' }];
    },

    renderHTML({ HTMLAttributes }) {
        return ['span', mergeAttributes(HTMLAttributes, { 'data-comment': '', class: 'codoc-comment' }), 0];
    },
});
