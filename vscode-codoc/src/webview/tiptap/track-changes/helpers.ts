import type { Editor } from '@tiptap/core';
import type { TrackedChangeInfo, NodeChangeTracking } from './types';

/**
 * Get all tracked changes from the current document state.
 */
export function getTrackedChanges(editor: Editor): TrackedChangeInfo[] {
  const changes: TrackedChangeInfo[] = [];
  const seen = new Set<string>();

  editor.state.doc.descendants((node, pos) => {
    // Check dataTracked attribute (paragraph insertions, boundary deletions, node type changes)
    if (node.isBlock && node.attrs.dataTracked) {
      const tracking = node.attrs.dataTracked as NodeChangeTracking;
      const key = `${tracking.changeId}-dataTracked-${pos}`;
      if (!seen.has(key)) {
        seen.add(key);
        // Map tracking types to change info types
        let type: TrackedChangeInfo['type'];
        if (tracking.originalType === 'paragraphInserted') {
          type = 'insertion';
        } else if (tracking.originalType === 'boundaryDeleted') {
          type = 'deletion';
        } else {
          type = 'nodeChange';
        }
        changes.push({
          changeId: tracking.changeId,
          type,
          authorId: tracking.authorId,
          authorName: tracking.authorName,
          authorColor: tracking.authorColor,
          timestamp: tracking.timestamp,
          from: pos,
          to: pos + node.nodeSize,
          text: node.textContent ?? '',
        });
      }
    }

    if (!node.isText) return;

    for (const mark of node.marks) {
      if (
        mark.type.name !== 'insertion' &&
        mark.type.name !== 'deletion' &&
        mark.type.name !== 'formatChange'
      ) {
        continue;
      }

      const changeId = mark.attrs.changeId;
      const key = `${changeId}-${mark.type.name}-${pos}`;
      if (seen.has(key)) continue;
      seen.add(key);

      changes.push({
        changeId,
        type: mark.type.name as 'insertion' | 'deletion' | 'formatChange',
        authorId: mark.attrs.authorId,
        authorName: mark.attrs.authorName,
        authorColor: mark.attrs.authorColor,
        timestamp: mark.attrs.timestamp,
        from: pos,
        to: pos + node.nodeSize,
        text: node.text ?? '',
        ...(mark.type.name === 'formatChange' ? {
          formatAdded: mark.attrs.formatAdded ?? undefined,
          formatRemoved: mark.attrs.formatRemoved ?? undefined,
        } : {}),
      });
    }
  });

  return changes;
}

/**
 * Group tracked changes by changeId (e.g., a replacement has both insertion + deletion
 * with the same changeId).
 */
export function getGroupedChanges(
  editor: Editor,
): Map<string, TrackedChangeInfo[]> {
  const changes = getTrackedChanges(editor);
  const groups = new Map<string, TrackedChangeInfo[]>();

  for (const change of changes) {
    const group = groups.get(change.changeId);
    if (group) {
      group.push(change);
    } else {
      groups.set(change.changeId, [change]);
    }
  }

  return groups;
}

/**
 * Get the "base text" — the document as it would appear if all pending changes
 * were rejected. (Original text without any insertions, with deletions restored.)
 */
export function getBaseText(editor: Editor): string {
  let text = '';
  let isFirstBlock = true;
  editor.state.doc.descendants((node) => {
    if (node.isBlock && node.isTextblock) {
      // In "base" (reject all), paragraphInserted blocks would be joined back
      // — so skip the newline for them
      const tracking = node.attrs.dataTracked as NodeChangeTracking | null;
      if (tracking?.originalType === 'paragraphInserted') {
        return; // no separator — this split would be undone
      }
      if (!isFirstBlock) {
        text += '\n';
      }
      isFirstBlock = false;
      return;
    }

    if (!node.isText) return;

    const hasInsertion = node.marks.some(
      (m) => m.type.name === 'insertion',
    );
    if (hasInsertion) return; // Skip inserted text — it's not in the base

    // Include all other text (including deletion-marked text, which IS in the base)
    text += node.text ?? '';
  });

  return text;
}

/**
 * Get the "result text" — the document as it would appear if all pending changes
 * were accepted. (With insertions applied and deletions removed.)
 */
export function getResultText(editor: Editor): string {
  let text = '';
  let isFirstBlock = true;
  editor.state.doc.descendants((node) => {
    if (node.isBlock && node.isTextblock) {
      // In "result" (accept all), boundaryDeleted blocks would be joined
      // — so skip the newline for them
      const tracking = node.attrs.dataTracked as NodeChangeTracking | null;
      if (tracking?.originalType === 'boundaryDeleted') {
        return; // no separator — this boundary would be removed
      }
      if (!isFirstBlock) {
        text += '\n';
      }
      isFirstBlock = false;
      return;
    }

    if (!node.isText) return;

    const hasDeletion = node.marks.some(
      (m) => m.type.name === 'deletion',
    );
    if (hasDeletion) return; // Skip deleted text

    text += node.text ?? '';
  });

  return text;
}

/**
 * Count pending changes.
 */
export function getPendingChangeCount(editor: Editor): number {
  const changeIds = new Set<string>();

  editor.state.doc.descendants((node) => {
    // Check dataTracked attribute
    if (node.isBlock && node.attrs.dataTracked) {
      const tracking = node.attrs.dataTracked as NodeChangeTracking;
      changeIds.add(tracking.changeId);
    }

    if (!node.isText) return;
    for (const mark of node.marks) {
      if (
        mark.type.name === 'insertion' ||
        mark.type.name === 'deletion' ||
        mark.type.name === 'formatChange'
      ) {
        changeIds.add(mark.attrs.changeId);
      }
    }
  });

  return changeIds.size;
}
