/**
 * protocol.ts — message + payload types shared between the extension host
 * (tree-editor.ts) and the bundled webview client (doc-view.ts).
 *
 * Type-only: imported with `import type` on the client so nothing here pulls
 * runtime code (or `vscode`) into the webview bundle.
 */

import type { DocSection, FeaturePhase } from '../state/doc-layout';

/** A node in the left tree pane (navigation). Mirrors the live feature tree
 *  plus injected ADD/MOVE ghost rows. */
export interface UINode {
    id: string;
    title: string;
    parent_id: string | null;
    retired: boolean;
    realized: boolean;
    refCount: number;
    bindings: { file: string; symbol: string }[];
    proposal: null | {
        op: 'retire' | 'amend' | 'add' | 'move';
        eventId: string;
        tag: string;
        title?: string | null;
        description?: string | null;
    };
    isProposal?: boolean;
    proposalOp?: 'add' | 'move';
    depth: number;
    children: string[];
    /** live agent activity on this feature (drives the pulsing dot) */
    activeMode?: 'write' | 'read' | null;
}

export interface SyncState {
    state: string;
    pending: number;
    activeWrite: string[];
    activeRead: string[];
    phase: Record<string, FeaturePhase>;
    realize?: { done: number; total: number; current: string };
}

export interface DocPayload {
    nodes: Record<string, UINode>;
    roots: string[];
    sections: DocSection[];
    status: { state: string; pending: number };
    sync: SyncState;
    rootName: string;
    pendingEventIds: string[];
    /** monotonic; the webview ignores any payload with a lower rev than the last */
    rev: number;
}

/** Messages the webview posts back to the host. */
export type WebviewMessage =
    | { kind: 'ready' }
    | { kind: 'edit-title'; featureId: string; newTitle: string }
    | { kind: 'edit-description'; featureId: string; newDescription: string }
    | { kind: 'move'; sourceId: string; newParentId: string | null }
    | { kind: 'open-text' }
    | { kind: 'open-binding'; file: string; symbol: string }
    | { kind: 'verdict'; eventIds: string[]; accept: boolean };
