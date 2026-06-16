import { Plugin, PluginKey, TextSelection } from '@tiptap/pm/state';
import type { EditorView } from '@tiptap/pm/view';
import type { Mark, Slice } from '@tiptap/pm/model';
import { AddMarkStep, RemoveMarkStep } from '@tiptap/pm/transform';
import type { Transaction } from '@tiptap/pm/state';
import type { ChangeAuthor, NodeChangeTracking } from './types';
import {
  generateChangeId,
  lastGraphemeClusterLength,
  firstGraphemeClusterLength,
} from './utils';

// Detect undo/redo transactions. prosemirror-history sets a PluginKey-based
// meta on undo/redo transactions. We eagerly resolve the checker so
// appendTransaction (which is synchronous) can use it.
let _isHistoryTransaction: ((tr: Transaction) => boolean) | undefined;
try {
  // Dynamic import resolved eagerly at module load. If @tiptap/pm/history
  // is not installed, this is a no-op and we fall back to a simple heuristic.
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const historyModule = (Function('return typeof require !== "undefined" ? require : undefined')() as
    | ((id: string) => Record<string, unknown>)
    | undefined
  )?.('@tiptap/pm/history');
  if (historyModule?.isHistoryTransaction) {
    _isHistoryTransaction = historyModule.isHistoryTransaction as typeof _isHistoryTransaction;
  }
} catch {
  // history not available
}

function isHistoryTransaction(tr: Transaction): boolean {
  if (_isHistoryTransaction) return _isHistoryTransaction(tr);
  return false;
}

function createNodeTracking(
  author: ChangeAuthor,
  type: string,
  changeId?: string,
): NodeChangeTracking {
  return {
    changeId: changeId ?? generateChangeId(),
    authorId: author.id,
    authorName: author.name,
    authorColor: author.color,
    timestamp: new Date().toISOString(),
    originalType: type,
  };
}

export const trackChangesPluginKey = new PluginKey('trackChanges');

interface SuggestModePluginOptions {
  getMode: () => string;
  getAuthor: () => ChangeAuthor;
}

function createChangeAttrs(author: ChangeAuthor, changeId?: string) {
  return {
    changeId: changeId ?? generateChangeId(),
    authorId: author.id,
    authorName: author.name,
    authorColor: author.color,
    timestamp: new Date().toISOString(),
  };
}

/**
 * Mark a range as deleted, skipping text nodes that already carry a deletion mark.
 * Used by handleTextInsert, handleSelectionDelete, handleEnter, and applyDeletion
 * to avoid overwriting another author's existing deletion tracking.
 */
function markRangeAsDeleted(
  doc: import('@tiptap/pm/model').Node,
  tr: import('@tiptap/pm/state').Transaction,
  from: number,
  to: number,
  deletionMark: Mark,
): void {
  doc.nodesBetween(from, to, (node, pos) => {
    if (!node.isText) return;
    const nodeFrom = Math.max(from, pos);
    const nodeTo = Math.min(to, pos + node.nodeSize);
    const hasDeletion = node.marks.some((m) => m.type.name === 'deletion');
    if (!hasDeletion) {
      tr.addMark(nodeFrom, nodeTo, deletionMark);
    }
  });
}

function getAdjacentInsertionMark(
  view: EditorView,
  pos: number,
  authorId: string,
): Mark | null {
  if (pos <= 0) return null;
  const $pos = view.state.doc.resolve(pos);
  const nodeBefore = $pos.nodeBefore;
  if (nodeBefore && nodeBefore.isText) {
    const insertionMark = nodeBefore.marks.find(
      (m) => m.type.name === 'insertion' && m.attrs.authorId === authorId,
    );
    if (insertionMark) {
      return insertionMark;
    }
  }
  return null;
}

function handleTextInsert(
  view: EditorView,
  from: number,
  to: number,
  text: string,
  author: ChangeAuthor,
): boolean {
  const { state } = view;
  const schema = state.schema;
  const tr = state.tr;

  // Normalize to NFC to prevent decomposed diacritics (e.g., Vietnamese ệ)
  // from splitting across mark boundaries as separate combining characters.
  const normalizedText = text.normalize('NFC');

  if (from !== to) {
    // Replacement: mark selected text as deleted, insert new text with insertion mark
    const changeId = generateChangeId();
    const deletionMark = schema.marks.deletion.create(
      createChangeAttrs(author, changeId),
    );
    const insertionMark = schema.marks.insertion.create(
      createChangeAttrs(author, changeId),
    );

    // Mark selected text as deleted, but skip ranges already carrying a deletion mark
    markRangeAsDeleted(state.doc, tr, from, to, deletionMark);

    // Insert new text after the deleted text
    tr.insertText(normalizedText, to);

    // Mark the inserted text
    tr.addMark(to, to + normalizedText.length, insertionMark);

    tr.setSelection(TextSelection.create(tr.doc, to + normalizedText.length));
  } else {
    // Pure insertion at cursor — reuse the exact adjacent mark to prevent
    // ProseMirror from splitting text into separate <ins> elements.
    // This is critical for complex scripts (Khmer, Thai, Arabic, etc.)
    // where syllable components must stay in the same DOM element for
    // the browser's text shaping engine to render them correctly.
    const existingMark = getAdjacentInsertionMark(view, from, author.id);
    const insertionMark = existingMark ?? schema.marks.insertion.create(
      createChangeAttrs(author),
    );

    tr.insertText(normalizedText, from);
    tr.addMark(from, from + normalizedText.length, insertionMark);
    tr.setSelection(TextSelection.create(tr.doc, from + normalizedText.length));
  }

  tr.setMeta(trackChangesPluginKey, { handled: true });
  view.dispatch(tr);
  return true;
}

function handleBackspace(view: EditorView, author: ChangeAuthor): boolean {
  const { state } = view;
  const { selection } = state;

  if (!selection.empty) {
    return handleSelectionDelete(view, author);
  }

  const $from = selection.$from;
  if ($from.parentOffset === 0) {
    // At start of paragraph — track cross-block join (backspace at boundary)
    return handleBlockJoinBackward(view, author);
  }

  const textBefore = $from.parent.textBetween(0, $from.parentOffset);
  const clusterLen = lastGraphemeClusterLength(textBefore);
  const deleteFrom = $from.pos - clusterLen;
  const deleteTo = $from.pos;

  return applyDeletion(view, deleteFrom, deleteTo, author);
}

function handleDelete(view: EditorView, author: ChangeAuthor): boolean {
  const { state } = view;
  const { selection } = state;

  if (!selection.empty) {
    return handleSelectionDelete(view, author);
  }

  const $to = selection.$to;
  const parentSize = $to.parent.content.size;
  if ($to.parentOffset >= parentSize) {
    // At end of paragraph — track cross-block join (delete at boundary)
    return handleBlockJoinForward(view, author);
  }

  const textAfter = $to.parent.textBetween(
    $to.parentOffset,
    parentSize,
  );
  const clusterLen = firstGraphemeClusterLength(textAfter);
  const deleteFrom = $to.pos;
  const deleteTo = $to.pos + clusterLen;

  return applyDeletion(view, deleteFrom, deleteTo, author, 'forward');
}

function handleBlockJoinBackward(view: EditorView, author: ChangeAuthor): boolean {
  const { state } = view;
  const { selection } = state;
  const $from = selection.$from;

  // We're at the start of a block. Add dataTracked to mark boundary deletion.
  const blockPos = $from.before($from.depth);

  // Can't join if we're at the first block in the document
  if (blockPos <= 0) return false;

  const node = state.doc.nodeAt(blockPos);
  if (!node) return false;

  // Already tracked? Skip to preserve existing tracking.
  if (node.attrs.dataTracked) return true;

  const tr = state.tr;
  const tracking = createNodeTracking(author, 'boundaryDeleted');
  tr.setNodeMarkup(blockPos, undefined, { ...node.attrs, dataTracked: tracking });
  tr.setMeta(trackChangesPluginKey, { handled: true });
  view.dispatch(tr);
  return true;
}

function handleBlockJoinForward(view: EditorView, author: ChangeAuthor): boolean {
  const { state } = view;
  const { selection } = state;
  const $to = selection.$to;

  const afterPos = $to.after($to.depth);
  if (afterPos >= state.doc.content.size) return false;

  const nextNode = state.doc.nodeAt(afterPos);
  if (!nextNode || !nextNode.isBlock) return false;

  // Already tracked? Skip to preserve existing tracking.
  if (nextNode.attrs.dataTracked) return true;

  const tr = state.tr;
  const tracking = createNodeTracking(author, 'boundaryDeleted');
  tr.setNodeMarkup(afterPos, undefined, { ...nextNode.attrs, dataTracked: tracking });
  tr.setMeta(trackChangesPluginKey, { handled: true });
  view.dispatch(tr);
  return true;
}

function handleEnter(view: EditorView, author: ChangeAuthor): boolean {
  const { state } = view;
  const { selection } = state;
  const tr = state.tr;
  const schema = state.schema;

  // If there's a selection, delete it first (as tracked change), then split
  if (!selection.empty) {
    const { from, to } = selection;
    const changeId = generateChangeId();
    const deletionMark = schema.marks.deletion.create(
      createChangeAttrs(author, changeId),
    );

    let hasOwnInsertions = false;
    state.doc.nodesBetween(from, to, (node) => {
      if (node.isText) {
        const ownInsertion = node.marks.find(
          (m) => m.type.name === 'insertion' && m.attrs.authorId === author.id,
        );
        if (ownInsertion) hasOwnInsertions = true;
      }
    });

    if (hasOwnInsertions) {
      const ranges: Array<{ from: number; to: number; isOwnInsertion: boolean; isAlreadyDeleted: boolean }> = [];
      state.doc.nodesBetween(from, to, (node, pos) => {
        if (!node.isText) return;
        const nodeFrom = Math.max(from, pos);
        const nodeTo = Math.min(to, pos + node.nodeSize);
        const ownInsertion = node.marks.find(
          (m) => m.type.name === 'insertion' && m.attrs.authorId === author.id,
        );
        const hasDeletion = node.marks.some((m) => m.type.name === 'deletion');
        ranges.push({ from: nodeFrom, to: nodeTo, isOwnInsertion: !!ownInsertion, isAlreadyDeleted: hasDeletion });
      });
      for (let i = ranges.length - 1; i >= 0; i--) {
        const range = ranges[i];
        if (range.isOwnInsertion) {
          tr.delete(range.from, range.to);
        } else if (!range.isAlreadyDeleted) {
          tr.addMark(range.from, range.to, deletionMark);
        }
      }
    } else {
      markRangeAsDeleted(state.doc, tr, from, to, deletionMark);
    }

    const splitPos = tr.mapping.map(from);
    tr.split(splitPos);

    // Add dataTracked to the new (second) paragraph
    const newParaPos = splitPos + 1;
    const newNode = tr.doc.nodeAt(newParaPos);
    if (newNode) {
      const tracking = createNodeTracking(author, 'paragraphInserted');
      tr.setNodeMarkup(newParaPos, undefined, { ...newNode.attrs, dataTracked: tracking });
    }
  } else {
    const pos = selection.from;
    const $pos = state.doc.resolve(pos);

    if ($pos.parentOffset === 0) {
      // At start of paragraph — split creates empty paragraph before cursor
      tr.split(pos);
      // Mark the first (empty) paragraph as inserted
      const $splitPos = tr.doc.resolve(pos);
      const emptyParaPos = $splitPos.before($splitPos.depth);
      const emptyNode = tr.doc.nodeAt(emptyParaPos);
      if (emptyNode && !emptyNode.attrs.dataTracked) {
        const tracking = createNodeTracking(author, 'paragraphInserted');
        tr.setNodeMarkup(emptyParaPos, undefined, { ...emptyNode.attrs, dataTracked: tracking });
      }
    } else {
      // At middle or end of paragraph — split and mark second paragraph
      tr.split(pos);
      const newParaPos = pos + 1;
      const newNode = tr.doc.nodeAt(newParaPos);
      if (newNode) {
        const tracking = createNodeTracking(author, 'paragraphInserted');
        tr.setNodeMarkup(newParaPos, undefined, { ...newNode.attrs, dataTracked: tracking });
      }
    }
  }

  // Place cursor at start of the new (second) paragraph
  try {
    const newCursorPos = selection.empty
      ? selection.from + 2
      : tr.mapping.map(selection.from) + 2;
    tr.setSelection(TextSelection.create(tr.doc, newCursorPos));
  } catch {
    // If position is invalid, let ProseMirror handle cursor placement
  }

  tr.setMeta(trackChangesPluginKey, { handled: true });
  view.dispatch(tr);
  return true;
}

function handleSelectionDelete(
  view: EditorView,
  author: ChangeAuthor,
): boolean {
  const { state } = view;
  const { from, to } = state.selection;
  const tr = state.tr;

  const deletionMark = state.schema.marks.deletion.create(
    createChangeAttrs(author),
  );

  // Check for own insertions within the selection — actually delete those
  let hasOwnInsertions = false;
  state.doc.nodesBetween(from, to, (node, pos) => {
    if (node.isText) {
      const ownInsertion = node.marks.find(
        (m) => m.type.name === 'insertion' && m.attrs.authorId === author.id,
      );
      if (ownInsertion) {
        hasOwnInsertions = true;
      }
    }
  });

  if (hasOwnInsertions) {
    // Complex case: need to delete own insertions and mark others as deleted
    // Process from end to start to maintain positions
    const ranges: Array<{
      from: number;
      to: number;
      isOwnInsertion: boolean;
      isAlreadyDeleted: boolean;
    }> = [];

    state.doc.nodesBetween(from, to, (node, pos) => {
      if (!node.isText) return;
      const nodeFrom = Math.max(from, pos);
      const nodeTo = Math.min(to, pos + node.nodeSize);
      const ownInsertion = node.marks.find(
        (m) => m.type.name === 'insertion' && m.attrs.authorId === author.id,
      );
      const hasDeletion = node.marks.some((m) => m.type.name === 'deletion');
      ranges.push({
        from: nodeFrom,
        to: nodeTo,
        isOwnInsertion: !!ownInsertion,
        isAlreadyDeleted: hasDeletion,
      });
    });

    // Process from end to start
    for (let i = ranges.length - 1; i >= 0; i--) {
      const range = ranges[i];
      if (range.isOwnInsertion) {
        tr.delete(range.from, range.to);
      } else if (!range.isAlreadyDeleted) {
        tr.addMark(range.from, range.to, deletionMark);
      }
    }
  } else {
    // Simple case: mark selection as deleted, skipping already-deleted text
    markRangeAsDeleted(state.doc, tr, from, to, deletionMark);
  }

  // Track interior block boundaries as boundaryDeleted for cross-block selections
  const $selFrom = state.doc.resolve(from);
  const $selTo = state.doc.resolve(to);
  if ($selFrom.depth > 0 && $selTo.depth > 0) {
    const startBlockEnd = $selFrom.end($selFrom.depth);
    if (to > startBlockEnd + 1) {
      // Selection spans multiple blocks — mark interior block nodes
      state.doc.nodesBetween(from, to, (node, pos) => {
        if (!node.isBlock || !node.isTextblock) return;
        // Skip the first block (it's the starting block)
        if (pos <= $selFrom.before($selFrom.depth)) return;
        if (!node.attrs.dataTracked) {
          const mappedPos = tr.mapping.map(pos);
          const mappedNode = tr.doc.nodeAt(mappedPos);
          if (mappedNode) {
            const tracking = createNodeTracking(author, 'boundaryDeleted');
            tr.setNodeMarkup(mappedPos, undefined, { ...mappedNode.attrs, dataTracked: tracking });
          }
        }
      });
    }
  }

  tr.setSelection(TextSelection.create(tr.doc, tr.mapping.map(from)));
  tr.setMeta(trackChangesPluginKey, { handled: true });
  view.dispatch(tr);
  return true;
}

function handleMultiLinePaste(
  view: EditorView,
  from: number,
  to: number,
  text: string,
  author: ChangeAuthor,
): boolean {
  const { state } = view;
  const schema = state.schema;
  const tr = state.tr;
  const lines = text.split('\n');

  // If there's a selection, mark it as deleted first
  if (from !== to) {
    const changeId = generateChangeId();
    const deletionMark = schema.marks.deletion.create(
      createChangeAttrs(author, changeId),
    );
    markRangeAsDeleted(state.doc, tr, from, to, deletionMark);
  }

  // Insert the first line at the insertion point (after any deleted selection).
  // All subsequent positions are tracked in the CURRENT tr.doc space (not original).
  const insertPos = to;
  const firstLine = lines[0].normalize('NFC');

  if (firstLine) {
    const lineMark = schema.marks.insertion.create(createChangeAttrs(author));
    tr.insertText(firstLine, insertPos);
    tr.addMark(insertPos, insertPos + firstLine.length, lineMark);
  }

  // Track cursor in current-document coordinates (NOT original space)
  let cursor = insertPos + firstLine.length;

  // For subsequent lines, split and insert with tracking
  for (let i = 1; i < lines.length; i++) {
    // Split at current position to create a new paragraph
    tr.split(cursor);
    const newParaPos = cursor + 1;

    // Mark the new paragraph as inserted
    const newNode = tr.doc.nodeAt(newParaPos);
    if (newNode) {
      const tracking = {
        changeId: generateChangeId(),
        authorId: author.id,
        authorName: author.name,
        authorColor: author.color,
        timestamp: new Date().toISOString(),
        originalType: 'paragraphInserted',
      };
      tr.setNodeMarkup(newParaPos, undefined, { ...newNode.attrs, dataTracked: tracking });
    }

    // Insert the line's text inside the new paragraph
    const line = lines[i].normalize('NFC');
    const textPos = newParaPos + 1; // inside the new paragraph
    if (line) {
      const lineMark = schema.marks.insertion.create(createChangeAttrs(author));
      tr.insertText(line, textPos);
      tr.addMark(textPos, textPos + line.length, lineMark);
      cursor = textPos + line.length;
    } else {
      cursor = textPos;
    }
  }

  // Set cursor at end of pasted content
  try {
    tr.setSelection(TextSelection.create(tr.doc, cursor));
  } catch {
    // If position is invalid, let ProseMirror handle cursor placement
  }

  tr.setMeta(trackChangesPluginKey, { handled: true });
  view.dispatch(tr);
  return true;
}

function applyDeletion(
  view: EditorView,
  deleteFrom: number,
  deleteTo: number,
  author: ChangeAuthor,
  direction: 'backward' | 'forward' = 'backward',
): boolean {
  const { state } = view;
  const tr = state.tr;

  // Check marks on the text we're about to "delete"
  let isOwnInsertion = false;
  let isAlreadyDeleted = false;

  state.doc.nodesBetween(deleteFrom, deleteTo, (node) => {
    if (node.isText) {
      const ownInsertion = node.marks.find(
        (m) => m.type.name === 'insertion' && m.attrs.authorId === author.id,
      );
      if (ownInsertion) {
        isOwnInsertion = true;
      }

      const deletion = node.marks.find((m) => m.type.name === 'deletion');
      if (deletion) {
        isAlreadyDeleted = true;
      }
    }
  });

  if (isOwnInsertion) {
    // Process each text node individually: delete own insertions, mark others as deleted
    const ranges: Array<{ from: number; to: number; isOwn: boolean; isAlreadyDeleted: boolean }> = [];
    state.doc.nodesBetween(deleteFrom, deleteTo, (node, pos) => {
      if (!node.isText) return;
      const nodeFrom = Math.max(deleteFrom, pos);
      const nodeTo = Math.min(deleteTo, pos + node.nodeSize);
      const ownIns = node.marks.find(
        (m) => m.type.name === 'insertion' && m.attrs.authorId === author.id,
      );
      const hasDeletion = node.marks.some((m) => m.type.name === 'deletion');
      ranges.push({ from: nodeFrom, to: nodeTo, isOwn: !!ownIns, isAlreadyDeleted: hasDeletion });
    });
    // Process from end to preserve positions
    for (let i = ranges.length - 1; i >= 0; i--) {
      const range = ranges[i];
      if (range.isOwn) {
        tr.delete(range.from, range.to);
      } else if (!range.isAlreadyDeleted) {
        const deletionMark = state.schema.marks.deletion.create(
          createChangeAttrs(author),
        );
        tr.addMark(range.from, range.to, deletionMark);
      }
    }
    tr.setMeta(trackChangesPluginKey, { handled: true });
    view.dispatch(tr);
    return true;
  }

  if (isAlreadyDeleted) {
    // Skip over already-deleted text in the appropriate direction
    const skipPos = findDeletionBoundary(
      view,
      direction === 'forward' ? deleteTo : deleteFrom,
      direction,
    );
    tr.setSelection(TextSelection.create(tr.doc, skipPos));
    view.dispatch(tr);
    return true;
  }

  // Mark text as deleted
  const deletionMark = state.schema.marks.deletion.create(
    createChangeAttrs(author),
  );
  tr.addMark(deleteFrom, deleteTo, deletionMark);
  tr.setSelection(TextSelection.create(tr.doc, deleteFrom));
  tr.setMeta(trackChangesPluginKey, { handled: true });
  view.dispatch(tr);
  return true;
}

function findDeletionBoundary(
  view: EditorView,
  pos: number,
  direction: 'backward' | 'forward',
): number {
  const { doc } = view.state;
  let currentPos = pos;

  if (direction === 'backward') {
    while (currentPos > 0) {
      const $pos = doc.resolve(currentPos);
      const nodeBefore = $pos.nodeBefore;
      if (!nodeBefore || !nodeBefore.isText) break;
      const hasDeletion = nodeBefore.marks.some(
        (m) => m.type.name === 'deletion',
      );
      if (!hasDeletion) break;
      currentPos -= nodeBefore.nodeSize;
    }
  } else {
    const docSize = doc.content.size;
    while (currentPos < docSize) {
      const $pos = doc.resolve(currentPos);
      const nodeAfter = $pos.nodeAfter;
      if (!nodeAfter || !nodeAfter.isText) break;
      const hasDeletion = nodeAfter.marks.some(
        (m) => m.type.name === 'deletion',
      );
      if (!hasDeletion) break;
      currentPos += nodeAfter.nodeSize;
    }
  }

  return currentPos;
}

export function createSuggestModePlugin(
  options: SuggestModePluginOptions,
): Plugin {
  return new Plugin({
    key: trackChangesPluginKey,

    props: {
      editable: () => options.getMode() !== 'view',

      handleTextInput(view, from, to, text) {
        if (options.getMode() !== 'suggest') return false;
        // Let native composition proceed uninterrupted for complex scripts
        // (Khmer, Thai, Arabic, CJK IME, etc.). The appendTransaction
        // safety net will add insertion marks after composition ends.
        if (view.composing) return false;
        return handleTextInsert(view, from, to, text, options.getAuthor());
      },

      handleKeyDown(view, event) {
        if (options.getMode() !== 'suggest') return false;
        // Don't intercept keys during composition
        if (view.composing) return false;

        if (event.key === 'Backspace') {
          return handleBackspace(view, options.getAuthor());
        }
        if (event.key === 'Delete') {
          return handleDelete(view, options.getAuthor());
        }
        if (event.key === 'Enter' && !event.shiftKey) {
          return handleEnter(view, options.getAuthor());
        }

        return false;
      },

      handlePaste(view, event, _slice: Slice) {
        if (options.getMode() !== 'suggest') return false;

        let text = event.clipboardData?.getData('text/plain');
        if (!text) {
          // Extract plain text from HTML paste (use DOMParser to avoid script execution)
          const html = event.clipboardData?.getData('text/html');
          if (html) {
            const parsed = new DOMParser().parseFromString(html, 'text/html');
            text = parsed.body.textContent || '';
          }
        }
        if (!text) return false;

        const { from, to } = view.state.selection;

        // Multi-line paste: split into paragraphs with tracked insertions
        if (text.includes('\n')) {
          return handleMultiLinePaste(view, from, to, text, options.getAuthor());
        }

        return handleTextInsert(view, from, to, text, options.getAuthor());
      },
    },

    // Safety net: catch text insertions that bypass handleTextInput
    // (e.g., IME composition for Khmer, Thai, CJK, etc.) and add insertion marks
    appendTransaction(transactions, _oldState, newState) {
      if (options.getMode() !== 'suggest') return null;

      // Skip transactions we already handled
      const isHandled = transactions.some((t) => t.getMeta(trackChangesPluginKey));
      if (isHandled) return null;

      // Skip undo/redo transactions — they restore previous state and should
      // not have insertion marks re-added by the safety net.
      const isHistoryTr = transactions.some((t) => isHistoryTransaction(t));
      if (isHistoryTr) return null;

      // Detect format changes (AddMarkStep/RemoveMarkStep for non-tracking marks).
      // This runs before the uiEvent check because format toggle commands
      // (e.g., toggleBold) don't set uiEvent meta but should still be tracked.
      const trackMarkNames = new Set(['insertion', 'deletion', 'formatChange']);
      const formatSteps: Array<{
        from: number;
        to: number;
        formatAdded: string | null;
        formatRemoved: string | null;
      }> = [];

      for (const transaction of transactions) {
        for (const step of transaction.steps) {
          if (step instanceof AddMarkStep) {
            const markStep = step as AddMarkStep;
            if (!trackMarkNames.has(markStep.mark.type.name)) {
              formatSteps.push({
                from: markStep.from,
                to: markStep.to,
                formatAdded: markStep.mark.type.name,
                formatRemoved: null,
              });
            }
          } else if (step instanceof RemoveMarkStep) {
            const markStep = step as RemoveMarkStep;
            if (!trackMarkNames.has(markStep.mark.type.name)) {
              formatSteps.push({
                from: markStep.from,
                to: markStep.to,
                formatAdded: null,
                formatRemoved: markStep.mark.type.name,
              });
            }
          }
        }
      }

      if (formatSteps.length > 0) {
        const tr = newState.tr;
        const author = options.getAuthor();
        for (const fs of formatSteps) {
          const formatChangeMark = newState.schema.marks.formatChange.create({
            ...createChangeAttrs(author),
            formatAdded: fs.formatAdded,
            formatRemoved: fs.formatRemoved,
          });
          tr.addMark(fs.from, fs.to, formatChangeMark);
        }
        tr.setMeta(trackChangesPluginKey, { handled: true });
        return tr;
      }

      // Only catch text insertion transactions from actual user input (e.g., IME composition).
      // Skip programmatic changes like setContent, acceptChange, rejectChange.
      // ProseMirror sets "uiEvent" meta on transactions from DOM input events.
      // Also check "composition" meta which is set for IME composition input.
      const isUserInput = transactions.some((t) => t.getMeta('uiEvent') || t.getMeta('composition'));
      if (!isUserInput) return null;

      // Check if document changed
      if (!transactions.some((t) => t.docChanged)) return null;

      // Collect changed ranges in the final document by examining each
      // transaction's step maps and mapping positions forward
      const changedRanges: Array<{ from: number; to: number }> = [];

      for (let i = 0; i < transactions.length; i++) {
        const transaction = transactions[i];
        if (!transaction.docChanged) continue;

        // Iterate through each step's map to find changed ranges
        transaction.steps.forEach((step, stepIndex) => {
          step.getMap().forEach(
            (_oldStart: number, _oldEnd: number, newStart: number, newEnd: number) => {
              if (newEnd <= newStart) return;

              // Map positions forward through remaining steps in this transaction
              let from = newStart;
              let to = newEnd;
              for (let s = stepIndex + 1; s < transaction.steps.length; s++) {
                from = transaction.steps[s].getMap().map(from, 1);
                to = transaction.steps[s].getMap().map(to, -1);
              }

              // Map through subsequent transactions
              for (let j = i + 1; j < transactions.length; j++) {
                from = transactions[j].mapping.map(from, 1);
                to = transactions[j].mapping.map(to, -1);
              }

              if (to > from) {
                changedRanges.push({ from, to });
              }
            },
          );
        });
      }

      if (changedRanges.length === 0) return null;

      const tr = newState.tr;
      let hasChanges = false;
      const author = options.getAuthor();

      // In changed ranges, find text without insertion marks and add them.
      // Reuse adjacent insertion marks from the same author to prevent
      // ProseMirror from creating separate <ins> elements that split
      // syllable clusters (critical for CJK IME, Korean, complex scripts).
      for (const range of changedRanges) {
        const clampedTo = Math.min(range.to, newState.doc.content.size);
        if (range.from >= clampedTo) continue;
        newState.doc.nodesBetween(range.from, clampedTo, (node, pos) => {
          if (!node.isText) return;

          // Skip text that already has track changes marks
          const hasTrackMark = node.marks.some(
            (m) => m.type.name === 'insertion' || m.type.name === 'deletion',
          );
          if (hasTrackMark) return;

          const nodeFrom = Math.max(range.from, pos);
          const nodeTo = Math.min(clampedTo, pos + node.nodeSize);

          if (nodeTo > nodeFrom) {
            // Try to reuse adjacent insertion mark to keep text in one element
            let insertionMark: Mark | null = null;
            if (nodeFrom > 0) {
              const $pos = newState.doc.resolve(nodeFrom);
              const nodeBefore = $pos.nodeBefore;
              if (nodeBefore?.isText) {
                insertionMark = nodeBefore.marks.find(
                  (m) => m.type.name === 'insertion' && m.attrs.authorId === author.id,
                ) ?? null;
              }
            }
            if (!insertionMark) {
              insertionMark = newState.schema.marks.insertion.create(
                createChangeAttrs(author),
              );
            }
            tr.addMark(nodeFrom, nodeTo, insertionMark);
            hasChanges = true;
          }
        });
      }

      if (hasChanges) {
        tr.setMeta(trackChangesPluginKey, { handled: true });
        return tr;
      }

      return null;
    },
  });
}
