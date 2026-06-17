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
import type { CommentThread } from '../state/comment-model';
import type { ResolvedCard } from '../state/registry-model';

/** An autocomplete candidate for the `@`-triggered code-reference picker (U5).
 *  Sourced from the sidecar `by_file` (bound symbols only). */
export interface RefSymbol {
    file: string;
    label: string;   // leaf name, e.g. `parse_text` (matches completion.ts)
    symbol: string;  // what goes after `#` in the link (kept == label)
    detail?: string; // `file · feature title`
}

/** A feature→feature thread target (a reads / used-by edge). `weight`/`kinds` ride
 *  along from `feature_edges` so the Connections panel ranks by coupling weight and
 *  picks a per-edge shape (shape = kind). Both are OPTIONAL on the wire: a stale /
 *  replayed payload from a prior build may omit them, so consumers default at read
 *  (`weight ?? 0`, `kinds ?? []`). The producer (assembleThreads) always sets them. */
export interface ThreadTarget { toId: string; toTitle: string; weight?: number; kinds?: string[] }
/** A code-ref thread target (a binding). */
export interface ThreadRef { file: string; symbol: string }
/** An external `[label](https://…)` link cited in a feature's description — the
 *  Consult strand (the realizing agent WebFetches these). */
export interface ThreadConsult { label: string; url: string }

/** The unified dependency "Connections" for one feature (U4 → U5): the four strands
 *  of the inline threads line / detail-pane panel under a heading — what it `reads`
 *  (Depends-on), what `usedBy` it, the code `refs` it binds (Bound code), and the
 *  external links to `consult`. The full (un-truncated, already-ranked) data, so the
 *  on-demand peek renders client-side with no extra round-trip. Assembled host-side
 *  from `feature_edges` (deps, with weights) + `by_feature` (bindings) + the
 *  description's external links (consult). `collapsed` reports, per strand, whether it
 *  exceeds the inline display cap (THREADS_COLLAPSE_AT) so the renderer shows a
 *  "show N more" affordance reusing the peek — a display swap, no transition.
 *  `consult`/`collapsed` are OPTIONAL on the wire: a stale / replayed payload from a
 *  prior build may omit them, so consumers default at read (`consult ?? []`,
 *  `collapsed?.reads ?? false`). The producer (assembleThreads) always sets them. */
export interface ThreadsData {
    reads: ThreadTarget[];
    usedBy: ThreadTarget[];
    refs: ThreadRef[];
    consult?: ThreadConsult[];
    collapsed?: { reads: boolean; usedBy: boolean; refs: boolean; consult: boolean };
}

/** The inline-display cap per strand; beyond it the strand collapses behind a
 *  "show N more" affordance (the peek shows the full ranked list). Shared by the
 *  assembler (sets `collapsed`) and the renderer (slices to it). */
export const THREADS_COLLAPSE_AT = 5;

/** Tier-1 hover-preview cards (U4), precomputed host-side from the registry +
 *  bindings sidecar via the pure `resolveCard` — the webview cannot read files or
 *  call Python, so it consumes these by lookup key:
 *   - `byRef`     keyed by `file#symbol` (a `codeRef` chip's exact target) and by
 *                 bare `file` (a file-only ref). The same `ResolvedCard` shape the
 *                 raw-text hover renders, so both surfaces share one contract.
 *   - `byFeature` keyed by feature id (a feature-title link hover). */
export interface HoverCards {
    byRef: Record<string, ResolvedCard>;
    byFeature: Record<string, ResolvedCard>;
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
    /** Inline comment threads (span-anchored steering notes). The editor renders a
     *  dotted underline + corner icon per thread and resolves the body popover
     *  client-side; the host owns their `> …` lifecycle. */
    comments?: CommentThread[];
    /** Tier-1 hover-preview cards keyed by ref target + feature id (U4). Assembled
     *  host-side from the registry + sidecar; the webview renders them on hover. */
    hoverCards?: HoverCards;
    /** Per-feature one-line pitch (FeatureMeta.pitch, B-U1) keyed by feature id — feeds
     *  glance mode (each feature collapses to its pitch). Derived from the sidecar. */
    pitches?: Record<string, string>;
    /** Feature ids "awaiting AI realization" — the daemon's doc-wins hold set
     *  (sidecar.holds: live doc-ahead intents ∪ queued realize directives). Drives the
     *  calm "being realized" badge in the single-surface model (U3); a faithful
     *  realization clears the feature from this set. Absent on legacy payloads. */
    awaitingAI?: string[];
    /** Features whose realization DIVERGED (U5): the agent changed this feature beyond
     *  the one you edited — flagged for "review what the AI did" (F3). `{fid → reason}`.
     *  The change itself renders as a pending proposal; this adds the at-a-glance cue.
     *  Faithful realizations are absent. Legacy payloads omit it. */
    divergent?: Record<string, string>;
    /** Persisted webview prefs: the glance-mode toggle, restored from workspaceState so
     *  it survives a reload. */
    prefs?: WebviewPrefs;
    /** monotonic; the webview ignores any payload with a lower rev than the last */
    rev: number;
}

/** Per-workspace webview preferences persisted in the host's `workspaceState`. */
export interface WebviewPrefs {
    /** glance mode is on — tree rows collapse to their one-line pitch. */
    glance: boolean;
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
    /** Withdraw a queued realization (U6): cancel feature `featureId`'s directive.
     *  The host appends a cancellation to edits.json; Loop B prunes the directive
     *  and releases the hold. The committed prose is kept. */
    | { kind: 'withdraw-realization'; featureId: string }
    | { kind: 'move'; sourceId: string; newParentId: string | null }
    | { kind: 'open-binding'; file: string; symbol: string }
    /** Open an external Consult link (a description's `https://` link) in the browser. */
    | { kind: 'open-link'; url: string }
    | { kind: 'verdict'; eventIds: string[]; accept: boolean }
    /** Create an inline comment: persist the thread + the doc (carrying its new
     *  `comment` mark) and queue the note as a `> …` steering line for the agent. */
    | { kind: 'comment-create'; doc: PMNode; thread: CommentThread }
    /** Edit a comment's body in place (the anchor + mark are unchanged). */
    | { kind: 'comment-edit'; id: string; body: string }
    /** Resolve / delete a comment: drop the thread + its `> …` line; the doc carries
     *  the mark removal. */
    | { kind: 'comment-resolve'; doc: PMNode; id: string }
    /** Persist a webview pref (glance toggle) into the host's per-workspace
     *  `workspaceState`. Decoration-only — never touches tree.doc.json. */
    | { kind: 'set-pref'; pref: 'glance'; value: boolean };
