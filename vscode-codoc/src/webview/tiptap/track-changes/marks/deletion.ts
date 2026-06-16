import { Mark, mergeAttributes } from '@tiptap/core';
import { sanitizeCSSValue } from '../utils';

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    deletion: {
      setDeletion: (attributes: Record<string, string>) => ReturnType;
      unsetDeletion: () => ReturnType;
    };
  }
}

export const DeletionMark = Mark.create({
  name: 'deletion',

  addOptions() {
    return {
      HTMLAttributes: {},
    };
  },

  addAttributes() {
    return {
      changeId: { default: null },
      authorId: { default: null },
      authorName: { default: null },
      authorColor: { default: null },
      timestamp: { default: null },
    };
  },

  inclusive: false,

  parseHTML() {
    return [{ tag: 'del[data-change-id]' }];
  },

  renderHTML({ HTMLAttributes }) {
    const safeColor = sanitizeCSSValue(HTMLAttributes.authorColor);
    return [
      'del',
      mergeAttributes(this.options.HTMLAttributes, {
        'data-change-id': HTMLAttributes.changeId,
        'data-author-id': HTMLAttributes.authorId,
        'data-author-name': HTMLAttributes.authorName,
        ...(safeColor ? { style: `--author-color: ${safeColor};` } : {}),
      }),
      0,
    ];
  },
});
