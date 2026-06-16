import { Mark, mergeAttributes } from '@tiptap/core';
import { sanitizeCSSValue } from '../utils';

export const FormatChangeMark = Mark.create({
  name: 'formatChange',

  // Allow multiple formatChange marks on the same text (e.g., bold added + italic removed)
  excludes: '',

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
      formatAdded: { default: null },
      formatRemoved: { default: null },
    };
  },

  inclusive: false,

  parseHTML() {
    return [{ tag: 'span[data-format-change]' }];
  },

  renderHTML({ HTMLAttributes }) {
    const title = [
      HTMLAttributes.formatAdded ? `Added: ${HTMLAttributes.formatAdded}` : '',
      HTMLAttributes.formatRemoved ? `Removed: ${HTMLAttributes.formatRemoved}` : '',
    ].filter(Boolean).join(', ');

    const safeColor = sanitizeCSSValue(HTMLAttributes.authorColor);
    return [
      'span',
      mergeAttributes(this.options.HTMLAttributes, {
        'data-format-change': 'true',
        'data-change-id': HTMLAttributes.changeId,
        'data-author-id': HTMLAttributes.authorId,
        'data-format-added': HTMLAttributes.formatAdded,
        'data-format-removed': HTMLAttributes.formatRemoved,
        title,
        ...(safeColor ? { style: `border-bottom: 2px dotted ${safeColor};` } : {}),
      }),
      0,
    ];
  },
});
