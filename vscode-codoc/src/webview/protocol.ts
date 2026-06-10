/**
 * protocol.ts — message + payload types shared between the extension host
 * (tree-editor.ts) and the bundled webview client (doc-view.ts).
 *
 * Type-only: imported with `import type` on the client so nothing here pulls
 * runtime code (or `vscode`) into the webview bundle.
 */

import type { FeaturePhase } from '../state/doc-layout';
import type { PMNode } from '../state/pm-doc';
import type { Suggestion } from '../state/suggestion-model';

/** An autocomplete candidate for the `@`-triggered code-reference picker (U5).
 *  Sourced from the sidecar `by_file` (bound symbols only). */
export interface RefSymbol {
    file: string;
    label: string;   // leaf name, e.g. `parse_text` (matches completion.ts)
    symbol: string;  // what goes after `#` in the link (kept == label)
    detail?: string; // `file · feature title`
}

/** A feature→feature dependency edge (host-internal; folded into ThreadsData). */
export interface FeatureDep {
    toId: string;
    toTitle: string;
    rel: 'depends' | 'usedby';
}

/** A feature→feature thread target (a reads / used-by edge). */
export interface ThreadTarget { toId: string; toTitle: string }
/** A code-ref thread target (a binding). */
export interface ThreadRef { file: string; symbol: string }

/** The unified dependency "threads" for one feature (U4): the three strands of the
 *  inline threads line under a heading — what it `reads`, what `usedBy` it, and the
 *  code `refs` it binds. The full (un-truncated) data, so the on-demand peek renders
 *  client-side with no extra round-trip. Assembled host-side from `feature_edges`
 *  (deps) + `by_feature` (bindings). */
export interface ThreadsData {
    reads: ThreadTarget[];
    usedBy: ThreadTarget[];
    refs: ThreadRef[];
}

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
    status: { state: string; pending: number };
    sync: SyncState;
    rootName: string;
    pendingEventIds: string[];
    /** The authoritative whole-tree rich doc (tree.doc.json, reconciled with the
     *  current structure). The webview mounts the editor from it so authorship
     *  marks survive. Absent on legacy payloads. */
    doc?: PMNode;
    /** Bound-symbol autocomplete candidates for the `@` code-ref picker (U5). */
    symbols?: RefSymbol[];
    /** Unified pending diffs: code-ahead (agent → human, accept/reject) + doc-ahead
     *  (human → agent, awaiting implementation). Rendered as persistent inline
     *  word-level diffs that only clear on resolution by the correct party. */
    suggestions?: Suggestion[];
    /** Per-feature unified dependency threads (reads / used-by / code refs) for the
     *  inline threads line under each heading + the on-demand peek (U4). */
    threads?: Record<string, ThreadsData>;
    /** monotonic; the webview ignores any payload with a lower rev than the last */
    rev: number;
}

/** Messages the webview posts back to the host. */
export type WebviewMessage =
    | { kind: 'ready' }
    /** Whole-doc settle (R3): the entire edited ProseMirror doc. The host persists
     *  it to tree.doc.json and serializes it to canonical tree.codoc, driving the
     *  existing parse→diff→apply pipeline (AMEND / MOVE / ADD / RETIRE). */
    | { kind: 'doc-settle'; doc: PMNode }
    /** Suggesting mode: persist captured doc-ahead suggestions (await the agent). */
    | { kind: 'suggest-create'; suggestions: Suggestion[] }
    /** Withdraw a pending doc-ahead suggestion by id. */
    | { kind: 'suggest-withdraw'; id: string }
    /** Apply a doc-ahead suggestion: settle its change into tree.codoc (the agent
     *  then implements via the existing Loop B realize path). */
    | { kind: 'suggest-apply'; id: string }
    | { kind: 'move'; sourceId: string; newParentId: string | null }
    | { kind: 'open-binding'; file: string; symbol: string }
    | { kind: 'verdict'; eventIds: string[]; accept: boolean };
