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
import type { HoldDetail, HistoryEntry } from '../state/bindings-model';
import type { AskWalkthrough } from '../state/ask-model';
import type { ViewerInfo } from './viewer-status';
import type { CommandKind } from '../state/edits-channel';

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
        /** What accepting does to the CODE (grammar.consequenceOf): "build" for a
         *  plan placeholder, "remove" for a delete-code retire, null for the
         *  majority that only reconcile the tree to code that already exists. */
        writesCode?: 'build' | 'remove' | null;
        /** A verdict is recorded for this proposal and has not drained yet. */
        verdictPending?: boolean;
    };
    isProposal?: boolean;
    proposalOp?: 'add' | 'move';
    depth: number;
    children: string[];
    /** live agent activity on this feature (drives the pulsing dot) */
    activeMode?: 'write' | 'read' | null;
    /** This node's own BCP-47 tag, present only when it differs from the tree's
     *  `docLanguage` (a deliberately bilingual tree). Becomes a `lang` attribute on
     *  the row and its prose, so the browser picks fonts and line-breaking per
     *  element rather than from one document tag half the content contradicts. */
    lang?: string;
}

/** One line in a feature's live agent-action ribbon (e.g. "editing agent.py",
 *  "running pytest", "git commit"). Derived from activity.json `recent`; `done`
 *  marks a step the agent moved past. `kind` distinguishes action steps
 *  (test/git) from file touches so the ribbon can style them. */
export interface AgentStep { label: string; done: boolean; kind?: string }

/** A description the loop rewrote unasked (sidecar `auto_edits`). Mirrors
 *  state/bindings-model.AutoEdit; re-declared here so the protocol stays the one
 *  contract the hub and the extension both compile against. */
export interface AutoEditInfo {
    at: string;
    prev: string;
    written_by: string;
    rationale: string;
}

/** A `codoc translate` run's live progress (`.codoc/translate.json`, written per
 *  batch by `loop/translate.py`). `pending` is the skeleton set — exactly the fids
 *  still awaiting their batch; each leaves the list the moment its fate is decided
 *  (translated or skipped), and the per-batch re-render replaces its skeleton with
 *  the translated prose. `running` is lease-guarded host-side (a crashed run's
 *  stale `true` is reported as not running). Absent when no run has ever
 *  happened / the last one is long done. */
export interface TranslationProgress {
    running: boolean;
    /** BCP-47 target ("zh-Hans") + its display name. */
    target: string;
    targetName: string;
    total: number;
    translated: number;
    /** Nodes this run refused (dropped citation, sibling collision, …). */
    skipped: { feature_id: string; title: string; reason: string }[];
    /** Fids still awaiting their batch — the webview's skeleton set. */
    pending: string[];
}

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
    /** W3: whether an agent session (activity epoch) is live right now — gates the
     *  pending-badge wording ("lands next agent turn" vs "run /codoc:sync"). */
    sessionLive?: boolean;
    /** W1: the role id of the coding agent on this epoch (claude-code | codex | …) —
     *  drives the presence avatar name/tint + ribbon "who". Absent ⇒ 'claude'. */
    agent?: string;
}

export interface DocPayload {
    nodes: Record<string, UINode>;
    roots: string[];
    status: { state: string; pending: number };
    /** The language the TREE is authored in (sidecar `doc_language`). Stamped on the
     *  document root as `lang`, shown in the toolbar switcher, and used as the
     *  fallback for any node with no `lang` of its own. Absent on a legacy payload,
     *  which is indistinguishable from an English tree — the correct fallback. */
    docLanguage?: { code: string; name: string; script?: string };
    /** The language tags a viewer may switch the tree to (built-in profiles). The
     *  host supplies the list so the webview never hard-codes a language table that
     *  could drift from `codoc/doclang.py`. */
    docLanguageChoices?: { code: string; name: string }[];
    sync: SyncState;
    rootName: string;
    pendingEventIds: string[];
    /** Monotonic id of the projection this payload was rendered from (#4). The webview
     *  echoes it back on `doc-settle` / `commit` so the host diffs the settled doc against
     *  the EXACT baseline the editor was showing — not whatever projection has since
     *  arrived — which prevents a settle computed pre-payload from reading a
     *  daemon-added feature as a user deletion (a phantom retire). */
    baselineId?: number;
    /** What THIS viewer may do (hub only; absent in VS Code, where the answer is
     *  always "everything"). Attached per connection rather than built into the
     *  shared payload — see codoc/serve/payload.py:viewer_block — so one viewer's
     *  authority is never served to another. The client renders affordances from
     *  it instead of drawing the maintainer's UI for everyone and letting the hub
     *  refuse the result. */
    viewer?: ViewerInfo;
    /** The authoritative whole-tree rich doc (tree.doc.json, reconciled with the
     *  current structure). The webview mounts the editor from it so authorship
     *  marks survive. Absent on legacy payloads. */
    doc?: PMNode;
    /** Bound-symbol autocomplete candidates for the `@` code-ref picker (U5). */
    symbols?: RefSymbol[];
    /** v6: descriptions the LOOP rewrote unasked, per feature. The one automatic op
     *  the reader is told about — refresh/attach/detach are machinery and are
     *  deliberately absent (see render._auto_edits). */
    autoEdits?: Record<string, AutoEditInfo>;
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
    /** W2 (blame): per-feature edit history (who/when/why), newest first, keyed by
     *  feature id. Only features changed within the daemon's scan window appear.
     *  Consumed by the History stance + the heading hover timeline. */
    history?: Record<string, HistoryEntry[]>;
    /** A live (or just-finished) `codoc translate` run — drives the per-node
     *  skeleton shimmer, the read-only guard on still-pending nodes, and the
     *  toolbar progress line. Absent when no run is in play. */
    translation?: TranslationProgress;
    /** The `/codoc:ask` walkthrough currently on screen (`.codoc/ask.json`), or
     *  absent for none. A pure VIEW: the numbered path is drawn over features that
     *  already exist, nothing about it is authored state, and dismissing it leaves
     *  the tree byte-identical — which is what lets it arrive at any point in an
     *  edit without a gate of its own. */
    ask?: AskWalkthrough | null;
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
    /** A HOST-RESOLVED, directly-loadable URL for an `image` block's attachment
     *  (VS Code: `asWebviewUri`; the hub: `/api/media/<name>`). The webview never
     *  resolves `content` (a repo-relative ref) into a URL itself — that
     *  resolution is host-specific, so it renders `mediaSrc` verbatim when
     *  present and falls back to a placeholder when absent (e.g. the ref is
     *  missing/unreadable). Omitted for every other kind. */
    mediaSrc?: string;
}

/** Per-workspace webview preferences persisted in the host's `workspaceState`. */
export interface WebviewPrefs {
    /** glance mode is on — tree rows collapse to their one-line pitch. */
    glance: boolean;
    /** History (blame) stance is on — each feature shows who last changed it and
     *  when, with an attribution rail tinted by author role (W2). */
    blame?: boolean;
}

/** Messages the webview posts back to the host. */
export type WebviewMessage =
    /** The reader dwelled long enough on a feature the loop had rewritten to count as
     *  having read it (state/auto-edits.ts). Persisted host-side so it survives a reload. */
    | { kind: 'auto-edit-seen'; fid: string; at: string }
    | { kind: 'ready' }
    /** Whole-doc settle (R3 / U2b): the entire edited ProseMirror doc. The host diffs it
     *  against the projection baseline `baselineId` names (#4) to emit identity-keyed
     *  commands; the daemon's Loop B applies them and renders tree.codoc. */
    | { kind: 'doc-settle'; doc: PMNode; baselineId?: number }
    /** Stage & SEND (U4 — save = stage & send): the explicit ⌘S / Commit gesture. The host
     *  flushes this doc (settle against `baselineId` + mark drafts), then hands the staged
     *  code-implying edits to the agent in one step. The single send gesture; the debounced
     *  `doc-settle` only captures (records locally, never sends). */
    | { kind: 'commit'; doc: PMNode; baselineId?: number }
    /** Withdraw a queued realization (U6): cancel feature `featureId`'s directive.
     *  The host appends a cancellation to edits.json; Loop B prunes the directive
     *  and releases the hold. The committed prose is kept. */
    | { kind: 'withdraw-realization'; featureId: string }
    /** Hand off ALL held suggesting-mode drafts to the agent (U4): the one batch-commit
     *  action. The host clears the edits.json `drafts` set; the daemon's next Loop B
     *  pass derives every held directive's `handed_off` as true and writes realize.md
     *  (the agent trigger). Prose stays exactly as committed. */
    | { kind: 'hand-off' }
    /** The tree pane's drag-to-reparent GESTURE (VS Code home): the host turns it into a
     *  `move` command. It is deliberately not the `move` command itself — the network home
     *  emits that directly (command-emitter.ts), and one wire name meaning two shapes is
     *  how the hub ended up appending a move with no feature id to edits.json. */
    | { kind: 'tree-move'; sourceId: string; newParentId: string | null }
    /** An identity-keyed authored command (U3), posted DIRECTLY by the client. Only the
     *  NETWORK home does this: in VS Code the extension host derives commands from a
     *  `doc-settle` because it owns the projection baselines, while on the hub the browser
     *  is the only party that sees a projection at all (see command-emitter.ts). The wire
     *  shape mirrors the Python `Command` dataclass as `serve/dispatch._command` reads it;
     *  the per-kind capability gate is server-side (KTD10). */
    | {
          kind: CommandKind;
          id: string;
          featureId?: string;
          localId?: string;
          baseText?: string;
          session?: string;
          payload?: {
              title?: string;
              description?: string;
              parent_id?: string | null;
              after_id?: string;
              before_id?: string;
          };
      }
    | { kind: 'open-binding'; file: string; symbol: string }
    /** Open an external Consult link (a description's `https://` link) in the browser. */
    | { kind: 'open-link'; url: string }
    /** Accept/Reject a proposal. `edits` (accept only) carries the author's
     *  amendments to an EDITABLE ghost — the daemon applies the proposal with the
     *  edited title/description in place of the proposed text. */
    | { kind: 'verdict'; eventIds: string[]; accept: boolean;
        edits?: { title?: string; description?: string } }
    /** The reader's verdict on an unasked loop rewrite (the in-situ auto-edit
     *  diff). Keep = acknowledge (the mark clears, prose stays). Revert = restore
     *  `prev` via a set_description command — a DOC edit the daemon classifies,
     *  which (since the code already changed) can queue reconciliation work. */
    | { kind: 'auto-edit-verdict'; fid: string; at: string; keep: boolean; prev: string }
    /** Stage 2 of the language switch: run `codoc translate` toward `code` (the
     *  workspace setting was already switched by stage 1's `set-doc-language`).
     *  The host spawns the CLI; progress arrives via `.codoc/translate.json`. */
    | { kind: 'translate-tree'; code: string }
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
    | { kind: 'set-pref'; pref: 'glance' | 'blame'; value: boolean }
      /** Change the language the tree is AUTHORED in. The host writes
       *  `.codoc/config.json` (authored settings, not derived state), which the
       *  daemon re-reads on its next pass — so this changes what NEW and AMENDED
       *  prose comes out in, and never rewrites what is already there. */
    | { kind: 'set-doc-language'; code: string }
    /** Live cross-surface bridge (P2 / §A.1): the user is editing feature `fid`'s prose —
     *  open its primary binding file Beside (non-focus-stealing) and light the implicated
     *  decl lines green. The host resolves file/symbols/lines from the sidecar bindings, so
     *  the webview only sends the fid. Debounced 180 ms after the last keystroke. */
    | { kind: 'bridge-open'; fid: string; reveal?: boolean }
    /** Clear the code-side bridge highlight when the caret leaves the feature (§A.1). The
     *  code pane STAYS open (opening is eager, closing is the user's call). */
    | { kind: 'bridge-dim'; fid: string | null }
    /** A typed-media block was authored/edited/removed (v6). Keyed by the STABLE
     *  block id (KTD8) — a pure reorder never sends this. `mediaData`/`mediaMime`
     *  (base64) carry an optional file attachment (image/pdf `add`) the host
     *  writes under `.codoc/media/` before persisting the block-edit; text-only
     *  kinds (diagram/latex/url) omit them. */
    /** Dismiss the `/codoc:ask` walkthrough: the host deletes `.codoc/ask.json`.
     *  Deleting is the whole teardown — the overlay owns no other state — so this
     *  can never leave anything half-cleared. */
    | { kind: 'ask-dismiss' }
    | {
          kind: 'block-edit';
          block: {
              block_id: string;
              feature_id: string;
              kind: string;
              action: 'edit' | 'add' | 'remove';
              content?: string;
              prev_content?: string;
              mediaData?: string;
              mediaMime?: string;
          };
      };

/** Messages the HOST posts to the webview. The webview message bus keys on `kind`. */
export type HostMessage =
    /** The full doc payload (the common case). */
    | { kind: 'doc'; payload: DocPayload }
    /** Code→doc spark (P2 / §A.3): a bound source file was edited — light up these feature
     *  headings with a travelling inbound glyph + a brief tree-row pulse. `big` marks fids
     *  whose change was large enough to likely re-question the prose (gets the divergent halo). */
    | { kind: 'code-touch'; fids: string[]; big?: string[] }
    /** Open the in-document find widget (the `codoc.find` command / ⌘F pressed
     *  while focus is outside the webview). `replace` opens it with the replace
     *  row already showing. */
    | { kind: 'find'; replace?: boolean };
