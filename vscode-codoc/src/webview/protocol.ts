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
import type { HoldDetail } from '../state/bindings-model';

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

/** One line in a feature's live agent-action ribbon (e.g. "editing agent.py").
 *  Derived from activity.json `touched`; `done` marks a step the agent moved past. */
export interface AgentStep { label: string; done: boolean }

export interface SyncState {
    state: string;
    pending: number;
    activeWrite: string[];
    activeRead: string[];
    phase: Record<string, FeaturePhase>;
    realize?: { done: number; total: number; current: string };
    /** Per-feature ordered agent-action steps for the inline ribbon (P2b). Optional on
     *  the wire — a stale payload / a host not tracking touches omits it. */
    steps?: Record<string, AgentStep[]>;
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
    /** Per-held-feature detail (kind + plain-language intent gloss) for the in-situ
     *  pending-intent decoration's hover title — a subset of `awaitingAI` (only features
     *  with a queued directive carry it). Lets the author confirm WHAT codoc understood,
     *  not just that something is queued. Absent on legacy payloads. */
    holdDetail?: Record<string, HoldDetail>;
    /** Features whose realization DIVERGED (U5): the agent changed this feature beyond
     *  the one you edited — flagged for "review what the AI did" (F3). `{fid → reason}`.
     *  The change itself renders as a pending proposal; this adds the at-a-glance cue.
     *  Faithful realizations are absent. Legacy payloads omit it. */
    divergent?: Record<string, string>;
    /** Suggesting-mode DRAFTS pending hand-off (U4): code-implying edits the human made
     *  that the daemon is HOLDING out of the agent queue until the human commits them
     *  with the one "hand to agent" action. The intersection of the host's draft set
     *  (edits.json `drafts`) with the live hold set (`awaitingAI`) — so a prose-only
     *  edit (no directive → never held) raises no hand-off affordance, and a handed-off
     *  edit (drafts cleared) drops out even while the agent is still realizing it.
     *  Absent/empty on legacy payloads and outside suggesting mode. */
    drafts?: string[];
    /** Persisted webview prefs: the glance-mode toggle, restored from workspaceState so
     *  it survives a reload. */
    prefs?: WebviewPrefs;
    /** Per-feature typed-media blocks (v6) — diagrams, images, latex, urls — keyed by
     *  feature id, ordered by `ord`. Persistent blocks only (prose is the description;
     *  transient blocks ride the steers channel). The webview renders each by `kind`
     *  below the feature; an unknown kind degrades to an inert placeholder. Absent on
     *  legacy payloads / features with no typed media. */
    blocks?: Record<string, UIBlock[]>;
    /** Hand-authored node client id → minted fid (v6). The webview patches a freshly
     *  minted fid onto the exact in-progress node by its localId, instead of guessing
     *  by title/order (which spawned duplicate/orphan nodes + caret jumps). */
    mintedByLocalId?: Record<string, string>;
    /** monotonic; the webview ignores any payload with a lower rev than the last */
    rev: number;
}

/** A typed-media block surfaced to the webview (v6). Mirrors the sidecar
 *  `blocks` slice / `bindings-model.ts:BlockEntry`. `id` is stable (KTD8): the
 *  webview MUST preserve it across edits so identity is never inferred from
 *  content. `content` is opaque — its schema belongs to the plugin named by `kind`. */
export interface UIBlock {
    id: string;
    kind: string;
    content: string;
    lifecycle: 'persistent' | 'transient';
    provenance: 'human' | 'agent' | 'derived';
    ord: number;
}

/** Per-workspace webview preferences persisted in the host's `workspaceState`. */
export interface WebviewPrefs {
    /** glance mode is on — tree rows collapse to their one-line pitch. */
    glance: boolean;
}

/** Messages the webview posts back to the host. */
export type WebviewMessage =
    | { kind: 'ready' }
    /** Whole-doc settle (R3 / U2b): the entire edited ProseMirror doc. The host
     *  persists it to tree.doc.json (single writer); the daemon's Loop B derives the
     *  AMEND / MOVE / ADD / RETIRE op from it and renders tree.codoc. */
    | { kind: 'doc-settle'; doc: PMNode }
    /** Stage & SEND (U4 — save = stage & send): the explicit ⌘S / Commit gesture. The host
     *  flushes this doc (persist + mark drafts), then hands the staged code-implying edits
     *  to the agent in one step (settle + hand-off). The single send gesture; the debounced
     *  `doc-settle` only captures (records locally, never sends). */
    | { kind: 'commit'; doc: PMNode }
    /** Withdraw a queued realization (U6): cancel feature `featureId`'s directive.
     *  The host appends a cancellation to edits.json; Loop B prunes the directive
     *  and releases the hold. The committed prose is kept. */
    | { kind: 'withdraw-realization'; featureId: string }
    /** Hand off ALL held suggesting-mode drafts to the agent (U4): the one batch-commit
     *  action. The host clears the edits.json `drafts` set; the daemon's next Loop B
     *  pass derives every held directive's `handed_off` as true and writes realize.md
     *  (the agent trigger). Prose stays exactly as committed. */
    | { kind: 'hand-off' }
    | { kind: 'move'; sourceId: string; newParentId: string | null }
    | { kind: 'open-binding'; file: string; symbol: string }
    /** Open an external Consult link (a description's `https://` link) in the browser. */
    | { kind: 'open-link'; url: string }
    | { kind: 'verdict'; eventIds: string[]; accept: boolean }
    /** Create an inline comment: persist the thread + the doc (carrying its new
     *  `comment` mark) and queue the note as a `> …` steering line for the agent.
     *  `mediaData` (base64) + `mediaMime` carry an optional TRANSIENT screenshot
     *  attachment (U6) — the host writes the bytes under `.codoc/media/` and sets
     *  `thread.media.ref`; the bytes never enter tree.doc.json. */
    | { kind: 'comment-create'; doc: PMNode; thread: CommentThread; mediaData?: string; mediaMime?: string }
    /** Edit a comment's body in place (the anchor + mark are unchanged). */
    | { kind: 'comment-edit'; id: string; body: string }
    /** Resolve / delete a comment: drop the thread + its `> …` line; the doc carries
     *  the mark removal. */
    | { kind: 'comment-resolve'; doc: PMNode; id: string }
    /** Persist a webview pref (glance toggle) into the host's per-workspace
     *  `workspaceState`. Decoration-only — never touches tree.doc.json. */
    | { kind: 'set-pref'; pref: 'glance'; value: boolean }
    /** Live cross-surface bridge (P2 / §A.1): the user is editing feature `fid`'s prose —
     *  open its primary binding file Beside (non-focus-stealing) and light the implicated
     *  decl lines green. The host resolves file/symbols/lines from the sidecar bindings, so
     *  the webview only sends the fid. Debounced 180 ms after the last keystroke. */
    | { kind: 'bridge-open'; fid: string }
    /** Clear the code-side bridge highlight when the caret leaves the feature (§A.1). The
     *  code pane STAYS open (opening is eager, closing is the user's call). */
    | { kind: 'bridge-dim'; fid: string | null };

/** Messages the HOST posts to the webview. The webview message bus keys on `kind`. */
export type HostMessage =
    /** The full doc payload (the common case). */
    | { kind: 'doc'; payload: DocPayload }
    /** Code→doc spark (P2 / §A.3): a bound source file was edited — light up these feature
     *  headings with a travelling inbound glyph + a brief tree-row pulse. `big` marks fids
     *  whose change was large enough to likely re-question the prose (gets the divergent halo). */
    | { kind: 'code-touch'; fids: string[]; big?: string[] };
