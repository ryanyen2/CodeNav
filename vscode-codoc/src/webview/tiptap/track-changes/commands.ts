import type { RawCommands } from '@tiptap/core';
import { TextSelection } from '@tiptap/pm/state';
import { canJoin } from '@tiptap/pm/transform';
import type {
  ChangeAuthor,
  TrackChangesMode,
  NodeChangeTracking,
  TrackChangesStorage,
} from './types';

import { generateChangeId } from './utils';
import { trackChangesPluginKey } from './suggest-mode-plugin';

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    trackChanges: {
      setSuggestMode: () => ReturnType;
      setEditMode: () => ReturnType;
      setViewMode: () => ReturnType;
      setTrackChangesMode: (mode: TrackChangesMode) => ReturnType;
      setTrackChangesAuthor: (author: ChangeAuthor) => ReturnType;
      acceptChange: (changeId: string) => ReturnType;
      rejectChange: (changeId: string) => ReturnType;
      acceptAll: () => ReturnType;
      rejectAll: () => ReturnType;
      trackSetNode: (typeName: string, attrs?: Record<string, unknown>) => ReturnType;
    };
  }

  interface Storage {
    trackChanges: TrackChangesStorage;
  }
}

export const trackChangesCommands: Partial<RawCommands> = {
  setSuggestMode:
    () =>
    ({ editor, tr, dispatch }) => {
      editor.storage.trackChanges.mode = 'suggest';
      if (dispatch) {
        tr.setMeta('addToHistory', false);
        dispatch(tr);
      }
      return true;
    },

  setEditMode:
    () =>
    ({ editor, tr, dispatch }) => {
      editor.storage.trackChanges.mode = 'edit';
      if (dispatch) {
        tr.setMeta('addToHistory', false);
        dispatch(tr);
      }
      return true;
    },

  setViewMode:
    () =>
    ({ editor, tr, dispatch }) => {
      editor.storage.trackChanges.mode = 'view';
      if (dispatch) {
        tr.setMeta('addToHistory', false);
        dispatch(tr);
      }
      return true;
    },

  setTrackChangesMode:
    (mode: TrackChangesMode) =>
    ({ editor, tr, dispatch }) => {
      editor.storage.trackChanges.mode = mode;
      if (dispatch) {
        tr.setMeta('addToHistory', false);
        dispatch(tr);
      }
      return true;
    },

  setTrackChangesAuthor:
    (author: ChangeAuthor) =>
    ({ editor }) => {
      editor.storage.trackChanges.author = author;
      return true;
    },

  acceptChange:
    (changeId: string) =>
    ({ state, dispatch, editor }) => {
      if (!dispatch) return true;

      const tr = state.tr;
      const deletionRanges: Array<{ from: number; to: number }> = [];
      const insertionUnmarkRanges: Array<{ from: number; to: number }> = [];
      const formatChangeRanges: Array<{ from: number; to: number }> = [];
      // dataTracked nodes: paragraphInserted, boundaryDeleted, or node type changes
      const dataTrackedNodes: Array<{ pos: number; tracking: NodeChangeTracking }> = [];

      // Find all nodes with this changeId
      state.doc.descendants((node, pos) => {
        if (!node.isText) {
          if (node.isBlock && node.attrs.dataTracked) {
            const tracking = node.attrs.dataTracked as NodeChangeTracking;
            if (tracking.changeId === changeId) {
              dataTrackedNodes.push({ pos, tracking });
            }
          }
          return;
        }

        for (const mark of node.marks) {
          if (mark.attrs.changeId !== changeId) continue;

          const nodeFrom = pos;
          const nodeTo = pos + node.nodeSize;

          if (mark.type.name === 'insertion') {
            insertionUnmarkRanges.push({ from: nodeFrom, to: nodeTo });
          } else if (mark.type.name === 'deletion') {
            deletionRanges.push({ from: nodeFrom, to: nodeTo });
          } else if (mark.type.name === 'formatChange') {
            formatChangeRanges.push({ from: nodeFrom, to: nodeTo });
          }
        }
      });

      // Process dataTracked nodes (from end to preserve positions)
      const sortedDataTracked = dataTrackedNodes.sort((a, b) => b.pos - a.pos);
      for (const { pos, tracking } of sortedDataTracked) {
        const mappedPos = tr.mapping.map(pos);
        const node = tr.doc.nodeAt(mappedPos);
        if (!node) continue;

        if (tracking.originalType === 'paragraphInserted') {
          // Accept paragraph insertion: clear tracking (split stays)
          tr.setNodeMarkup(mappedPos, undefined, { ...node.attrs, dataTracked: null });
        } else if (tracking.originalType === 'boundaryDeleted') {
          // Accept boundary deletion: join paragraphs
          tr.setNodeMarkup(mappedPos, undefined, { ...node.attrs, dataTracked: null });
          if (canJoin(tr.doc, mappedPos)) {
            tr.join(mappedPos);
          }
        } else {
          // Accept node type change: clear tracking (keep new type)
          tr.setNodeMarkup(mappedPos, undefined, { ...node.attrs, dataTracked: null });
        }
      }

      // Remove insertion marks (text stays) — map through tr.mapping in case
      // earlier dataTracked joins shifted positions
      for (const range of insertionUnmarkRanges) {
        const mappedFrom = tr.mapping.map(range.from);
        const mappedTo = tr.mapping.map(range.to);
        tr.removeMark(mappedFrom, mappedTo, state.schema.marks.insertion);
      }

      // Remove format change marks (formatting stays)
      for (const range of formatChangeRanges) {
        const mappedFrom = tr.mapping.map(range.from);
        const mappedTo = tr.mapping.map(range.to);
        tr.removeMark(mappedFrom, mappedTo, state.schema.marks.formatChange);
      }

      // Delete deletion-marked text (process from end to preserve positions)
      const sortedDeletions = deletionRanges.sort((a, b) => b.from - a.from);
      for (const range of sortedDeletions) {
        const mappedFrom = tr.mapping.map(range.from);
        const mappedTo = tr.mapping.map(range.to);
        tr.delete(mappedFrom, mappedTo);
      }

      if (
        insertionUnmarkRanges.length > 0 ||
        deletionRanges.length > 0 ||
        formatChangeRanges.length > 0 ||
        dataTrackedNodes.length > 0
      ) {
        tr.setMeta(trackChangesPluginKey, { handled: true });
        dispatch(tr);
        editor.storage.trackChanges.onStatusChange?.(changeId, 'accepted');
      }

      return true;
    },

  rejectChange:
    (changeId: string) =>
    ({ state, dispatch, editor }) => {
      if (!dispatch) return true;

      const tr = state.tr;
      const insertionDeleteRanges: Array<{ from: number; to: number }> = [];
      const deletionUnmarkRanges: Array<{ from: number; to: number }> = [];
      const formatChangeRanges: Array<{
        from: number;
        to: number;
        formatAdded: string | null;
        formatRemoved: string | null;
      }> = [];
      const dataTrackedNodes: Array<{ pos: number; tracking: NodeChangeTracking }> = [];

      state.doc.descendants((node, pos) => {
        if (!node.isText) {
          if (node.isBlock && node.attrs.dataTracked) {
            const tracking = node.attrs.dataTracked as NodeChangeTracking;
            if (tracking.changeId === changeId) {
              dataTrackedNodes.push({ pos, tracking });
            }
          }
          return;
        }

        for (const mark of node.marks) {
          if (mark.attrs.changeId !== changeId) continue;

          const nodeFrom = pos;
          const nodeTo = pos + node.nodeSize;

          if (mark.type.name === 'insertion') {
            insertionDeleteRanges.push({ from: nodeFrom, to: nodeTo });
          } else if (mark.type.name === 'deletion') {
            deletionUnmarkRanges.push({ from: nodeFrom, to: nodeTo });
          } else if (mark.type.name === 'formatChange') {
            formatChangeRanges.push({
              from: nodeFrom,
              to: nodeTo,
              formatAdded: mark.attrs.formatAdded,
              formatRemoved: mark.attrs.formatRemoved,
            });
          }
        }
      });

      // Remove deletion marks (text restored) — map through tr.mapping for consistency
      for (const range of deletionUnmarkRanges) {
        const mappedFrom = tr.mapping.map(range.from);
        const mappedTo = tr.mapping.map(range.to);
        tr.removeMark(mappedFrom, mappedTo, state.schema.marks.deletion);
      }

      // Process dataTracked nodes (from end to preserve positions)
      const sortedDataTracked = dataTrackedNodes.sort((a, b) => b.pos - a.pos);
      for (const { pos, tracking } of sortedDataTracked) {
        const mappedPos = tr.mapping.map(pos);
        const node = tr.doc.nodeAt(mappedPos);
        if (!node) continue;

        if (tracking.originalType === 'paragraphInserted') {
          // Reject paragraph insertion: join paragraphs (undo the split)
          tr.setNodeMarkup(mappedPos, undefined, { ...node.attrs, dataTracked: null });
          if (canJoin(tr.doc, mappedPos)) {
            tr.join(mappedPos);
          }
        } else if (tracking.originalType === 'boundaryDeleted') {
          // Reject boundary deletion: clear tracking (keep separate)
          tr.setNodeMarkup(mappedPos, undefined, { ...node.attrs, dataTracked: null });
        } else {
          // Reject node type change: revert to original type
          const originalType = state.schema.nodes[tracking.originalType];
          if (originalType) {
            const originalAttrs = tracking.originalAttrs ?? {};
            tr.setNodeMarkup(mappedPos, originalType, {
              ...node.attrs,
              ...originalAttrs,
              dataTracked: null,
            });
          }
        }
      }

      // Reject format changes: revert the formatting
      for (const range of formatChangeRanges) {
        const mappedFrom = tr.mapping.map(range.from);
        const mappedTo = tr.mapping.map(range.to);
        tr.removeMark(mappedFrom, mappedTo, state.schema.marks.formatChange);
        if (range.formatAdded) {
          const markType = state.schema.marks[range.formatAdded];
          if (markType) {
            tr.removeMark(mappedFrom, mappedTo, markType);
          }
        }
        if (range.formatRemoved) {
          const markType = state.schema.marks[range.formatRemoved];
          if (markType) {
            tr.addMark(mappedFrom, mappedTo, markType.create());
          }
        }
      }

      // Delete insertion-marked text (process from end to preserve positions)
      const sortedDeletions = insertionDeleteRanges.sort(
        (a, b) => b.from - a.from,
      );
      for (const range of sortedDeletions) {
        const mappedFrom = tr.mapping.map(range.from);
        const mappedTo = tr.mapping.map(range.to);
        tr.delete(mappedFrom, mappedTo);
      }

      if (
        insertionDeleteRanges.length > 0 ||
        deletionUnmarkRanges.length > 0 ||
        formatChangeRanges.length > 0 ||
        dataTrackedNodes.length > 0
      ) {
        tr.setMeta(trackChangesPluginKey, { handled: true });
        dispatch(tr);
        editor.storage.trackChanges.onStatusChange?.(changeId, 'rejected');
      }

      return true;
    },

  acceptAll:
    () =>
    ({ state, dispatch, editor }) => {
      if (!dispatch) return true;

      const tr = state.tr;
      const deletionRanges: Array<{ from: number; to: number }> = [];
      const boundaryDeletePositions: number[] = [];

      // First pass: handle dataTracked and text marks
      // Map positions through tr.mapping for robustness — setNodeMarkup with
      // type changes could theoretically shift positions for custom block types.
      state.doc.descendants((node, pos) => {
        if (node.isBlock && node.attrs.dataTracked) {
          const tracking = node.attrs.dataTracked as NodeChangeTracking;
          if (tracking.originalType === 'boundaryDeleted') {
            // Accept boundary deletion: join paragraphs (collect for later, from end)
            boundaryDeletePositions.push(pos);
          }
          // For all dataTracked: clear tracking
          const mappedPos = tr.mapping.map(pos);
          const mappedNode = tr.doc.nodeAt(mappedPos);
          if (mappedNode) {
            tr.setNodeMarkup(mappedPos, undefined, { ...mappedNode.attrs, dataTracked: null });
          }
        }

        if (!node.isText) return;
        for (const mark of node.marks) {
          const mappedFrom = tr.mapping.map(pos);
          const mappedTo = tr.mapping.map(pos + node.nodeSize);
          if (mark.type.name === 'insertion') {
            tr.removeMark(mappedFrom, mappedTo, state.schema.marks.insertion);
          }
          if (mark.type.name === 'deletion') {
            deletionRanges.push({
              from: pos,
              to: pos + node.nodeSize,
            });
          }
          if (mark.type.name === 'formatChange') {
            tr.removeMark(mappedFrom, mappedTo, state.schema.marks.formatChange);
          }
        }
      });

      // Accept boundary deletions: join paragraphs (from end)
      const sortedBoundaryDeletes = boundaryDeletePositions.sort((a, b) => b - a);
      for (const pos of sortedBoundaryDeletes) {
        const mappedPos = tr.mapping.map(pos);
        if (canJoin(tr.doc, mappedPos)) {
          tr.join(mappedPos);
        }
      }

      // Delete all deletion-marked text (from end to preserve positions)
      const sortedDeletions = deletionRanges.sort(
        (a, b) => b.from - a.from,
      );
      for (const range of sortedDeletions) {
        const mappedFrom = tr.mapping.map(range.from);
        const mappedTo = tr.mapping.map(range.to);
        tr.delete(mappedFrom, mappedTo);
      }

      if (deletionRanges.length > 0 || tr.steps.length > 0) {
        // Reset selection to valid position
        const newDoc = tr.doc;
        const maxPos = newDoc.content.size - 1;
        const safePos = Math.min(
          state.selection.from,
          maxPos > 0 ? maxPos : 1,
        );
        try {
          tr.setSelection(TextSelection.create(newDoc, safePos));
        } catch {
          tr.setSelection(TextSelection.create(newDoc, 1));
        }
        tr.setMeta(trackChangesPluginKey, { handled: true });
        dispatch(tr);
      }

      return true;
    },

  rejectAll:
    () =>
    ({ state, dispatch, editor }) => {
      if (!dispatch) return true;

      const tr = state.tr;
      const insertionDeleteRanges: Array<{ from: number; to: number }> = [];
      const paragraphInsertedPositions: number[] = [];

      // First pass: handle dataTracked and collect text changes
      // Map positions through tr.mapping for robustness — setNodeMarkup with
      // type changes could theoretically shift positions for custom block types.
      state.doc.descendants((node, pos) => {
        if (node.isBlock && node.attrs.dataTracked) {
          const tracking = node.attrs.dataTracked as NodeChangeTracking;
          if (tracking.originalType === 'paragraphInserted') {
            // Reject paragraph insertion: join paragraphs (collect for later, from end)
            paragraphInsertedPositions.push(pos);
          } else if (tracking.originalType === 'boundaryDeleted') {
            // Reject boundary deletion: clear tracking (keep separate)
            const mappedPos = tr.mapping.map(pos);
            const mappedNode = tr.doc.nodeAt(mappedPos);
            if (mappedNode) {
              tr.setNodeMarkup(mappedPos, undefined, { ...mappedNode.attrs, dataTracked: null });
            }
          } else {
            // Reject node type change: revert to original type
            const originalType = state.schema.nodes[tracking.originalType];
            if (originalType) {
              const mappedPos = tr.mapping.map(pos);
              const mappedNode = tr.doc.nodeAt(mappedPos);
              if (mappedNode) {
                tr.setNodeMarkup(mappedPos, originalType, {
                  ...mappedNode.attrs,
                  ...(tracking.originalAttrs ?? {}),
                  dataTracked: null,
                });
              }
            }
          }
        }

        if (!node.isText) return;
        for (const mark of node.marks) {
          const mappedFrom = tr.mapping.map(pos);
          const mappedTo = tr.mapping.map(pos + node.nodeSize);
          if (mark.type.name === 'deletion') {
            tr.removeMark(mappedFrom, mappedTo, state.schema.marks.deletion);
          }
          if (mark.type.name === 'insertion') {
            insertionDeleteRanges.push({
              from: pos,
              to: pos + node.nodeSize,
            });
          }
          if (mark.type.name === 'formatChange') {
            // Revert the formatting
            if (mark.attrs.formatAdded) {
              const markType = state.schema.marks[mark.attrs.formatAdded];
              if (markType) {
                tr.removeMark(mappedFrom, mappedTo, markType);
              }
            }
            if (mark.attrs.formatRemoved) {
              const markType = state.schema.marks[mark.attrs.formatRemoved];
              if (markType) {
                tr.addMark(mappedFrom, mappedTo, markType.create());
              }
            }
            tr.removeMark(mappedFrom, mappedTo, state.schema.marks.formatChange);
          }
        }
      });

      // Reject paragraph insertions: join paragraphs (undo splits, from end)
      const sortedParagraphInserts = paragraphInsertedPositions.sort((a, b) => b - a);
      for (const pos of sortedParagraphInserts) {
        const mappedPos = tr.mapping.map(pos);
        // Clear the tracking first
        const node = tr.doc.nodeAt(mappedPos);
        if (node) {
          tr.setNodeMarkup(mappedPos, undefined, { ...node.attrs, dataTracked: null });
        }
        if (canJoin(tr.doc, mappedPos)) {
          tr.join(mappedPos);
        }
      }

      // Delete all insertion-marked text (from end to preserve positions)
      const sortedDeletions = insertionDeleteRanges.sort(
        (a, b) => b.from - a.from,
      );
      for (const range of sortedDeletions) {
        const mappedFrom = tr.mapping.map(range.from);
        const mappedTo = tr.mapping.map(range.to);
        tr.delete(mappedFrom, mappedTo);
      }

      if (insertionDeleteRanges.length > 0 || tr.steps.length > 0) {
        const newDoc = tr.doc;
        const maxPos = newDoc.content.size - 1;
        const safePos = Math.min(
          state.selection.from,
          maxPos > 0 ? maxPos : 1,
        );
        try {
          tr.setSelection(TextSelection.create(newDoc, safePos));
        } catch {
          tr.setSelection(TextSelection.create(newDoc, 1));
        }
        tr.setMeta(trackChangesPluginKey, { handled: true });
        dispatch(tr);
      }

      return true;
    },

  trackSetNode:
    (typeName: string, attrs?: Record<string, unknown>) =>
    ({ state, dispatch, editor }) => {
      if (!dispatch) return true;

      const mode = editor.storage.trackChanges.mode;

      // View mode: no changes allowed
      if (mode === 'view') return false;

      // In edit mode, just do a normal setNodeMarkup
      if (mode !== 'suggest') {
        const { $from } = state.selection;
        const pos = $from.before($from.depth);
        const node = state.doc.nodeAt(pos);
        if (!node) return false;

        const nodeType = state.schema.nodes[typeName];
        if (!nodeType) return false;

        const tr = state.tr;
        tr.setNodeMarkup(pos, nodeType, { ...node.attrs, ...attrs });
        dispatch(tr);
        return true;
      }

      // In suggest mode, track the node type change
      const { $from } = state.selection;
      const pos = $from.before($from.depth);
      const node = state.doc.nodeAt(pos);
      if (!node) return false;

      const nodeType = state.schema.nodes[typeName];
      if (!nodeType) return false;

      // If already that type, nothing to do
      if (node.type.name === typeName) return false;

      const author = editor.storage.trackChanges.author;
      const tracking: NodeChangeTracking = {
        changeId: generateChangeId(),
        authorId: author.id,
        authorName: author.name,
        authorColor: author.color,
        timestamp: new Date().toISOString(),
        originalType: node.type.name,
        originalAttrs: { ...node.attrs },
      };

      // If already tracked, check if we're reverting to original
      if (node.attrs.dataTracked) {
        const existing = node.attrs.dataTracked as NodeChangeTracking;
        if (existing.originalType === typeName) {
          // Reverting to original — restore original attrs and remove tracking
          const tr = state.tr;
          tr.setNodeMarkup(pos, nodeType, {
            ...(existing.originalAttrs ?? {}),
            ...attrs,
            dataTracked: null,
          });
          dispatch(tr);
          return true;
        }
        // Changing to a third type — update tracking but keep original type
        tracking.originalType = existing.originalType;
        tracking.originalAttrs = existing.originalAttrs;
        tracking.changeId = existing.changeId;
      }

      const tr = state.tr;
      tr.setNodeMarkup(pos, nodeType, {
        ...node.attrs,
        ...attrs,
        dataTracked: tracking,
      });
      dispatch(tr);
      return true;
    },
};
