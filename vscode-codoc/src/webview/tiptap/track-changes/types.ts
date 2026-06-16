export interface ChangeAuthor {
  id: string;
  name: string;
  color: string;
}

export type TrackChangesMode = 'edit' | 'suggest' | 'view';

export interface TrackChangesOptions {
  author: ChangeAuthor;
  mode?: TrackChangesMode;
  onStatusChange?: (changeId: string, status: 'accepted' | 'rejected') => void;
  additionalBlockTypes?: string[];
}

export interface TrackChangesStorage {
  mode: TrackChangesMode;
  author: ChangeAuthor;
  onStatusChange?: (changeId: string, status: 'accepted' | 'rejected') => void;
}

export interface TrackedChangeInfo {
  changeId: string;
  type: 'insertion' | 'deletion' | 'formatChange' | 'nodeChange';
  authorId: string;
  authorName: string;
  authorColor: string;
  timestamp: string;
  from: number;
  to: number;
  text: string;
  formatAdded?: string;
  formatRemoved?: string;
}

export interface ChangeMarkAttributes {
  changeId: string;
  authorId: string;
  authorName: string;
  authorColor: string;
  timestamp: string;
}

export interface NodeChangeTracking {
  changeId: string;
  authorId: string;
  authorName: string;
  authorColor: string;
  timestamp: string;
  originalType: string;
  originalAttrs?: Record<string, unknown>;
}
