import { Extension } from '@tiptap/core';
import type { TrackChangesOptions, TrackChangesStorage } from './types';
import { InsertionMark } from './marks/insertion';
import { DeletionMark } from './marks/deletion';
import { FormatChangeMark } from './marks/format-change';
import { createSuggestModePlugin } from './suggest-mode-plugin';
import { trackChangesCommands } from './commands';

export const TrackChangesExtension = Extension.create<
  TrackChangesOptions,
  TrackChangesStorage
>({
  name: 'trackChanges',

  addOptions() {
    return {
      author: {
        id: 'anonymous',
        name: 'Anonymous',
        color: '#6b7280',
      },
      mode: 'edit',
      onStatusChange: undefined,
      additionalBlockTypes: [],
    };
  },

  addStorage() {
    return {
      mode: this.options.mode ?? 'edit',
      author: this.options.author,
      onStatusChange: this.options.onStatusChange,
    };
  },

  addGlobalAttributes() {
    return [
      {
        // Add dataTracked attribute to all block-level nodes for node-level
        // change tracking (e.g., paragraph -> heading type changes)
        types: [...new Set([
          'paragraph', 'heading', 'blockquote', 'codeBlock', 'listItem', 'bulletList', 'orderedList',
          ...(this.options.additionalBlockTypes ?? []),
        ])],
        attributes: {
          dataTracked: {
            default: null,
            parseHTML: (element: HTMLElement) => {
              const val = element.getAttribute('data-tracked');
              if (!val) return null;
              try {
                return JSON.parse(val);
              } catch {
                return null;
              }
            },
            renderHTML: (attributes: Record<string, unknown>) => {
              if (!attributes.dataTracked) return {};
              return { 'data-tracked': JSON.stringify(attributes.dataTracked) };
            },
          },
        },
      },
    ];
  },

  addExtensions() {
    return [InsertionMark, DeletionMark, FormatChangeMark];
  },

  addCommands() {
    return trackChangesCommands;
  },

  addProseMirrorPlugins() {
    const storage = this.storage;
    return [
      createSuggestModePlugin({
        getMode: () => storage.mode,
        getAuthor: () => storage.author,
      }),
    ];
  },
});
