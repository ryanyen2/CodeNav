export { TrackChangesExtension } from './extension';
export { InsertionMark } from './marks/insertion';
export { DeletionMark } from './marks/deletion';
export { FormatChangeMark } from './marks/format-change';
export { trackChangesPluginKey } from './suggest-mode-plugin';
export {
  getTrackedChanges,
  getGroupedChanges,
  getBaseText,
  getResultText,
  getPendingChangeCount,
} from './helpers';
export type {
  ChangeAuthor,
  TrackChangesMode,
  TrackChangesOptions,
  TrackChangesStorage,
  TrackedChangeInfo,
  ChangeMarkAttributes,
  NodeChangeTracking,
} from './types';
