/**
 * whole-doc-editor.ts — ONE TipTap editor over the entire feature tree.
 *
 * Headings are feature nodes; heading depth = tree structure. Editing a heading
 * renames the feature; Tab / Shift-Tab indent / outdent it (and its subtree)
 * within the tree; the body under a heading is its description (prose + `@`-refs +
 * marks). Edits settle (debounced) by handing the WHOLE doc to the host (onSettle),
 * which persists it to `tree.doc.json` (U2b single-writer — never tree.codoc); the
 * daemon's Loop B parses that doc and derives the AMEND / MOVE / ADD / RETIRE ops.
 *
 * ONE editing surface (U3): there is no Editing/Suggesting toggle and no pen/pencil
 * instrument. The human just edits; every edit COMMITS (settles). The daemon's
 * `classify.py` decides per-edit whether it implies code; a code-implying commit
 * lands the feature in the doc-wins hold set, which surfaces here as the calm
 * "being realized" badge (hold-decorations.ts) — not a client-side guess. The
 * agent→human review direction (tracked changes, accept/reject) lands in U4.
 */
import { Editor, Extension } from '@tiptap/core';
import { TextSelection } from '@tiptap/pm/state';
import { codocExtensions } from './schema';
import { AuthorStamp, AuthorController, REFLECT_META } from './author-plugin';
import {
    backspaceVerdict,
    deleteForwardVerdict,
    verdictTransaction,
    type BoundaryVerdict,
} from './block-boundary';
import { CodeRefSuggestion, RefSymbol } from './code-ref-suggestion';
import { newLocalId } from './local-id';
import {
    indentHeading,
    outdentHeading,
    newFeatureHeading,
    toggleRetireHeading,
    headingPosForFid,
} from './structure-commands';
import { SuggestionDecorations, SUGGESTIONS_UPDATED, DependencyDecorations, DEPS_UPDATED } from './suggestion-decorations';
import { AutoEditDecorations, AUTO_EDITS_UPDATED } from './auto-edit-decorations';
import { BusyDecorations, BUSY_UPDATED, type BusyInfo } from './busy-decorations';
import { railState, RAIL_STATE_LABEL, type RailSignals } from '../../state/feature-state';
import type { AutoEditInfo } from '../protocol';
import { ActivityDecorations, PHASES_UPDATED } from './activity-decorations';
import { BlameDecorations, BLAME_UPDATED } from './blame-decorations';
import type { HistoryEntry } from '../../state/bindings-model';
import { RevealDecorations, REVEAL_UPDATED } from './reveal-decorations';
import { AgentRibbon, STEPS_UPDATED } from './agent-ribbon';
import { HoldDecorations, HOLDS_UPDATED } from './hold-decorations';
import { BlockDecorations, BLOCKS_UPDATED, type BlockEditMsg } from './block-decorations';
import { BlockSuggestion } from './block-suggestion';
import type { UIBlock } from '../protocol';
import {
    CapturedDecorations, CAPTURED_UPDATED, featureBlocks, ftKey,
    rebaseCaptured, settledPendingFids, type FeatureText,
} from './captured-decorations';
import { GlanceDecorations, GLANCE_UPDATED } from './glance-decorations';
import { resetCommentDecorations } from './comment-decorations';
import { attachHoverCards, HoverCardData } from './hover-card';
import { renderTreeFromDoc } from '../../state/doc-serialize';
import { gateProjection, shouldDeferProjection } from '../doc-gate';
import { mintCommentId, CommentThread } from '../../state/comment-model';
import type { HoldDetail } from '../../state/bindings-model';
import type { Suggestion } from '../../state/suggestion-model';
import { inlineRunsToText, type PMNode } from '../../state/pm-doc';
import { tweenScrollTop, navDuration, muteWindowFor, prefersReducedMotion, staggerHover, sparkIn, type TweenController } from '../motion';
import { icon } from '../icons';
import type { FeaturePhase } from '../../state/activity-model';
import type { ThreadsData, AgentStep } from '../protocol';

export interface WholeDocEditorOptions {
    controller: AuthorController;
    getSymbols: () => RefSymbol[];
    /** Commit the whole settled doc (debounced). The single edit path — captures locally.
     *  `baselineId` is the projection this doc was ADOPTED from — the baseline its content
     *  was typed against, which the host diffs it and sources `base_text` from. The editor
     *  owns that citation because only it knows what it adopted: a settle flushed BY an
     *  arriving projection (setDoc) carries content from the PREVIOUS baseline, and reading
     *  the newest payload's id at post time was finding #2 — the daemon's in-flight changes
     *  then read as user edits that revert them. Undefined before the first adopt. */
    onSettle: (doc: PMNode, baselineId?: number) => void;
    /** Stage & SEND (U4): the explicit Save/Commit gesture (⌘S or the Commit button).
     *  Flushes the latest edit and hands the staged code-implying edits to the agent.
     *  Cites the adopted baseline for the same reason `onSettle` does. */
    onCommit?: (doc: PMNode, baselineId?: number) => void;
    /** `edits` — the author amended an editable ghost before accepting (see
     *  suggestion-decorations.SuggestionHandlers). */
    onAccept: (s: Suggestion, edits?: { title?: string; description?: string }) => void;
    onReject: (s: Suggestion) => void;
    /** Withdraw a queued realization for a feature (U6) — the ✕ on its "realizing"
     *  badge. Cancels the directive, keeps the prose. */
    onWithdrawRealization: (featureId: string) => void;
    onOpenBinding: (file: string, symbol: string) => void;
    /** Open a Consult strand link (a description's external `https://` page). */
    onConsult: (url: string) => void;
    /** Create an inline comment: the whole doc (carrying the new anchor mark) + the
     *  thread. `media` carries an optional TRANSIENT screenshot attachment (U6) as
     *  base64 bytes for the host to store under `.codoc/media/`. */
    onCommentCreate: (doc: PMNode, thread: CommentThread, media?: { data: string; mime: string }) => void;
    /** Edit a comment's body in place. */
    onCommentEdit: (id: string, body: string) => void;
    /** Resolve a comment: the whole doc (anchor mark removed) + the thread id. */
    onCommentResolve: (doc: PMNode, id: string) => void;
    /** The active feature changed — drives the tree-pane highlight, and (only when
     *  `source==='scroll'`) the eased tree re-center. `'selection'` fires on every caret move,
     *  so re-centering on it would animate the tree on every keystroke (KTD2). */
    onActiveFeature?: (fid: string | null, source: 'scroll' | 'selection') => void;
    /** v6→v7: the reader's explicit verdict on an unasked loop rewrite. Keep =
     *  acknowledge (the mark clears for good). Revert = restore `prev` through the
     *  authored-command channel (a real edit the daemon classifies — it can queue
     *  reconcile work now that the code has moved). Replaces the dwell-to-clear
     *  model, whose marks evaporated the moment the reader looked at them. */
    onAutoEditVerdict?: (fid: string, at: string, keep: boolean, prev: string) => void;
    /** Pointer hovering a depends-on / used-by link — drives a transient tree-pane
     *  highlight + scroll-to (preview navigation). null on leave. */
    onHoverFeature?: (fid: string | null) => void;
    /** The user edited the feature the caret is in (P2 / §A.1 doc→code bridge). Fires on
     *  every keystroke with the owning fid (null when not in a feature); the webview
     *  debounces it 180 ms and opens that feature's bound code Beside. */
    onEditFeature?: (fid: string | null) => void;
    /** A typed-media block (v6) was edited — handed to the host → edits.json → Loop B
     *  `lower`. A pure move (ord change) never fires this; only content edits. */
    onBlockEdit?: (edit: BlockEditMsg) => void;
}

export interface WholeDocEditorHandle {
    element: HTMLElement;
    /** Re-seed from an external payload (skipped while the user has unsettled edits).
     *  `baselineId` names the projection: it becomes this editor's citation once the doc
     *  is actually adopted, so every later settle diffs against what the author saw. */
    setDoc: (doc: PMNode, baselineId?: number) => void;
    /** Update the pending diff list (re-renders the inline diff decorations). */
    setSuggestions: (suggestions: Suggestion[]) => void;
    /** Update the per-feature dependency threads (reads / used-by / code refs). */
    setThreads: (threads: Record<string, ThreadsData>) => void;
    /** Update the inline comment threads (drives the anchor icons + popover). */
    setComments: (comments: CommentThread[]) => void;
    /** Update the precomputed tier-1 hover-preview cards (codeRef chips + feature links). */
    setHoverCards: (cards: HoverCardData | null) => void;
    /** Update the live agent-activity phases (hooks → activity.json → sync.phase). */
    setPhases: (phases: Record<string, FeaturePhase>) => void;
    setSteps: (steps: Record<string, AgentStep[]>) => void;
    /** v6: the unasked loop rewrites still owed attention (already seen-filtered). */
    setAutoEdits: (edits: Record<string, AutoEditInfo>) => void;
    /** Sections being rewritten under the reader RIGHT NOW (translation batches
     *  pending / the agent applying) — draws the skeleton shimmer and blocks user
     *  edits inside those sections until they land (busy-decorations.ts). */
    setBusy: (busy: Record<string, BusyInfo>) => void;
    setSessionLive: (live: boolean) => void;
    setHistory: (history: Record<string, HistoryEntry[]>) => void;
    setBlame: (on: boolean) => void;
    /** Update the currently-active agent's role — tints the ribbon + resolves its "who" label
     *  (state/presence.ts's roleName/roleInk), matching the presence avatar's tint. */
    setRole: (role: string) => void;
    /** Update the "awaiting AI realization" set (the daemon hold set) — drives the
     *  pending-intent rail + underline + being-realized badge. `detail` carries the
     *  queued directive's kind + intent gloss per feature (a subset of `fids`) for the
     *  rail's hover title; omit it (tests) for the plain rail. */
    setHeld: (fids: string[], detail?: Record<string, HoldDetail>) => void;
    /** Held drafts (U3): edits recorded & staged locally but NOT yet handed off — drives
     *  the "captured" mark (alongside the client-side changed-vs-baseline set). */
    setDrafts: (fids: string[]) => void;
    /** Per-feature typed-media blocks (v6) — diagrams/images/latex/urls rendered below
     *  each feature heading. Persistent only; ordered host-side. */
    setBlocks: (blocks: Record<string, UIBlock[]>) => void;
    /** localId→minted-fid map (v6) — the exact reconciliation table patchMintedIds uses
     *  to stamp a freshly-minted fid onto the right in-progress node. Set before setDoc. */
    setMintedMap: (m: Record<string, string>) => void;
    /** Per-feature one-line pitches (FeatureMeta.pitch) — feeds glance mode. */
    setPitches: (pitches: Record<string, string>) => void;
    /** Toggle glance mode (collapse each feature to its pitch). Decoration only. */
    setGlance: (on: boolean) => void;
    scrollToFeature: (fid: string) => void;
    /** Stage & send now (U4) — the Commit button's entry point; same as ⌘S in the editor. */
    commit: () => void;
    /** Mint a new top-level feature with `title` (P4 / §D.3 ⌘K "Create feature"). Appends a
     *  level-0 heading and commits so the host mints the fid. */
    createFeature: (title: string) => void;
    /** Caret position (selection.from) — persisted + restored across reload/reopen (U5). */
    getCaretPos: () => number;
    /** Restore the caret to an absolute position (clamped). Call AFTER the first setDoc settles
     *  so its heading-fallback placement doesn't clobber the restored caret (KTD3). */
    setCaretPos: (pos: number) => void;
    /** Doc surface scroll offset — persisted + restored across reload/reopen (U5). */
    getScrollTop: () => number;
    setScrollTop: (n: number) => void;
    isDirty: () => boolean;
    /** Code→doc spark (P2 / §A.3): a bound source file was edited — land an inbound glyph
     *  on each fid's heading, hold ~2.5 s, then settle to a persistent blue underline tick.
     *  `big` fids (a large change Loop A will likely re-question) also get the divergent halo.
     *  Reduced motion: the glyph just appears (sparkIn jumps to its final frame). */
    touchFeatures: (fids: string[], big?: Set<string>) => void;
    destroy: () => void;
}

const SETTLE_DEBOUNCE_MS = 1200;

function iconButton(label: string, title: string, onClick: () => void, cls = ''): HTMLButtonElement {
    const b = document.createElement('button');
    b.className = ('ce-btn ' + cls).trim();
    b.textContent = label;
    b.title = title;
    b.type = 'button';
    b.addEventListener('mousedown', ev => ev.preventDefault());
    b.addEventListener('click', ev => { ev.preventDefault(); onClick(); });
    return b;
}

/** Keymap for the outliner: Tab/Shift-Tab restructure; Enter in a heading drops to
 *  its description rather than splitting the heading; ⌘S/Ctrl-S stages & sends (U4). */
function makeKeymap(commit: () => void): Extension {
    return Extension.create({
        name: 'codocOutlinerKeymap',
        // Above StarterKit's base keymap (default 100): its Backspace/Delete bindings
        // are precisely the ones that would merge prose into a feature title, so our
        // boundary guard has to be offered the keystroke first or it never runs.
        priority: 1000,
        addKeyboardShortcuts() {
            const ed = this.editor;
            /** Run a block-boundary verdict: move the caret instead of merging across
             *  a heading, or return false to let ProseMirror's default deletion run. */
            const boundary = (verdict: (s: typeof ed.state) => BoundaryVerdict) => (): boolean => {
                const tr = verdictTransaction(ed.state, verdict(ed.state));
                if (!tr) return false;
                ed.view.dispatch(tr);
                return true;
            };
            return {
                Tab: () => indentHeading(ed),
                'Shift-Tab': () => outdentHeading(ed),
                // Every backward-deletion chord routes through the same guard, so a
                // title can only change by editing the title (see block-boundary.ts).
                Backspace: boundary(backspaceVerdict),
                'Mod-Backspace': boundary(backspaceVerdict),
                'Alt-Backspace': boundary(backspaceVerdict),
                Delete: boundary(deleteForwardVerdict),
                'Mod-Delete': boundary(deleteForwardVerdict),
                'Alt-Delete': boundary(deleteForwardVerdict),
                // ⌘S / Ctrl-S = "save the file" → stage & send (U4). The host never dirties
                // the text document (single-writer), so the native save is a no-op we
                // repurpose; returning true preventDefaults it so no save dialog flashes.
                // NOTE: a window-level capture-phase listener in doc-view.ts (U6) now swallows
                // this chord from ANY focus context (incl. the editor) BEFORE it reaches this
                // keymap, so in practice this binding is a fallback and never double-fires.
                'Mod-s': () => { commit(); return true; },
                Enter: () => {
                    const { $from } = ed.state.selection;
                    if ($from.parent.type.name !== 'featureHeading') return false; // normal paragraph split
                    // Insert a description paragraph right after the heading, move into it.
                    const headingEnd = $from.after($from.depth);
                    const para = ed.schema.nodes.paragraph.create();
                    const tr = ed.state.tr.insert(headingEnd, para);
                    tr.setSelection(TextSelection.near(tr.doc.resolve(headingEnd + 1)));
                    ed.view.dispatch(tr);
                    return true;
                },
            };
        },
    });
}

export function mountWholeDocEditor(container: HTMLElement, opts: WholeDocEditorOptions): WholeDocEditorHandle {
    const wrap = document.createElement('div');
    wrap.className = 'codoc-whole-editor';

    const toolbar = document.createElement('div');
    toolbar.className = 'ce-toolbar';

    const body = document.createElement('div');
    body.className = 'ce-body';
    const surface = document.createElement('div');
    surface.className = 'ce-surface ce-whole-surface';
    const rail = document.createElement('div');
    rail.className = 'ce-toc-rail';
    body.append(surface, rail);

    let dirty = false;
    let settleTimer = 0;
    let lastTypedAt = 0;                 // last USER keystroke (see activelyEditing)
    let suppressUpdate = false;          // true while we programmatically setContent
    let currentSuggestions: Suggestion[] = [];   // agent code-ahead proposals (sidecar)
    // Signature of the agent code-ahead AMENDs last LOADED into the doc as engine
    // marks. setDoc reloads when this changes even if the baseline text is identical
    // — so a newly-arrived agent proposal's marks appear, and a rejected one's marks
    // clear (reject leaves the baseline unchanged, so only the signature moves).
    let lastProposalsSig = '';
    let currentThreads: Record<string, ThreadsData> = {};
    let currentPhases: Record<string, FeaturePhase> = {};
    let currentSteps: Record<string, AgentStep[]> = {};   // P2b agent-action ribbon
    // v6→v7: unasked loop rewrites the reader has not RESOLVED yet. Cleared by an
    // explicit Keep/Restore verdict (auto-edit-decorations.ts) — never by merely
    // looking, which is how the record of an AI edit used to evaporate unreviewed.
    let currentAutoEdits: Record<string, AutoEditInfo> = {};
    // Sections being rewritten right now (translating / agent applying) — skeleton
    // shimmer + the per-section edit guard (busy-decorations.ts).
    let currentBusy = new Map<string, BusyInfo>();
    let currentRole = 'claude';   // the ribbon's "who" — matches the presence avatar's role
    let currentHeld = new Set<string>();   // handed-off features (staged & sent) → pending badge
    let currentSessionLive = false;        // W3: live agent session → "lands next turn" wording
    let currentBlame = false;              // W2: History (blame) stance on
    let currentHistory: Record<string, HistoryEntry[]> = {};  // W2: per-feature edit history
    let currentHoldDetail: Record<string, HoldDetail> = {};  // queued-directive {kind,intent} per held fid
    // Edit-lifecycle phase 1 (U3): the "captured" set is computed in the plugin from
    // (live doc vs baseline) ∪ drafts, minus handed-off. The baseline is the feature text
    // as of the LAST COMMIT (or last external reload) — frozen across the daemon's
    // self-echo round-trip so a captured edit (add OR delete) persists until ⌘S/Commit.
    let capturedBaseline = new Map<string, FeatureText>();
    let currentDrafts = new Set<string>();
    let currentBlocks: Record<string, UIBlock[]> = {};  // v6 typed-media blocks per feature
    let currentMintedByLocalId: Record<string, string> = {};  // v6 localId→minted fid (exact reconcile)
    let currentComments: CommentThread[] = [];
    let currentHoverCards: HoverCardData | null = null;
    let currentPitches: Record<string, string> = {}; // B-U2 glance: fid → pitch
    let glanceOn = false;
    // Last NON-EMPTY selection — a fallback so a bubble action still has a range to act
    // on if focus moved and the live selection collapsed (the "comment did nothing" bug).
    let lastSelection: { from: number; to: number } | null = null;

    // ── per-feature HLC version gate (U5 / R14 / KTD4) ────────────────────────────
    // Replaces the removed whole-doc docAhead/rev gate. `localVersions` is the
    // per-fid version (HLC `to_str()`) we last ADOPTED from a projection; `pendingFids`
    // is the set of fids with an un-acked local edit since that adopt. When a new
    // projection arrives, setDoc gates PER FEATURE (doc-gate.ts): a feature with no
    // pending edit always adopts the projection; a feature with a pending edit adopts
    // only when its projected version is strictly newer — else the optimistic local
    // copy is kept (no cross-feature clobber). Reset on a full reload (the in-memory
    // state is intentionally empty after a window reload → the first projection adopts).
    const localVersions = new Map<string, string>();
    const pendingFids = new Set<string>();
    // The projection baseline this editor's content is computed FROM — stamped at the
    // end of an adopt (setDoc), so it always trails the doc rather than leading it. Every
    // settle cites this, which is what makes a flush triggered BY an arriving projection
    // (setDoc's settleNow) diff against the text the author actually typed against (#2).
    let adoptedBaselineId: number | undefined;
    // The text of each feature as last ADOPTED from a projection. An edit that returns
    // to it has nothing left to protect, so the feature stops being pending — without
    // this an undo pins a feature pending forever and no projection can reach it again.
    const adoptedText = new Map<string, string>();

    const editor = new Editor({
        element: surface,
        extensions: [
            ...codocExtensions(),
            AuthorStamp.configure({ controller: opts.controller, now: () => Date.now() }),
            CodeRefSuggestion.configure({ getSymbols: opts.getSymbols, char: '@' }),
            SuggestionDecorations.configure({
                // Agent → human (code-ahead) proposals: amend diffs render from engine
                // marks in the doc (host-injected); this plugin anchors their
                // accept/reject affordance and the add/move/retire widgets. No
                // dual-state — the human never composes an inline suggestion (U3).
                getSuggestions: () => currentSuggestions,
                handlers: { accept: opts.onAccept, reject: opts.onReject },
                // A proposal on a feature the user has already edited (captured locally
                // or handed off) is contested — the strip says so rather than letting
                // Accept look like a formality that costs them nothing.
                getLocallyEdited: () => new Set([...currentDrafts, ...currentHeld]),
            }),
            AutoEditDecorations.configure({
                getUnseen: () => currentAutoEdits,
                // The explicit verdict pair (Keep / Restore). Optimistically clear the
                // local entry so the strip can't double-fire while the host round-trips.
                handlers: {
                    keep: (fid, at) => {
                        delete currentAutoEdits[fid];
                        editor.view.dispatch(editor.state.tr.setMeta(AUTO_EDITS_UPDATED, true));
                        scheduleRail();
                        opts.onAutoEditVerdict?.(fid, at, true, '');
                    },
                    revert: (fid, at, prev) => {
                        delete currentAutoEdits[fid];
                        editor.view.dispatch(editor.state.tr.setMeta(AUTO_EDITS_UPDATED, true));
                        scheduleRail();
                        opts.onAutoEditVerdict?.(fid, at, false, prev);
                    },
                },
            }),
            BusyDecorations.configure({ getBusy: () => currentBusy }),
            DependencyDecorations.configure({
                getThreads: () => currentThreads,
                onNavigate: fid => scrollToFeatureInternal(fid, true),
                onOpenBinding: opts.onOpenBinding,
                onConsult: opts.onConsult,
            }),
            ActivityDecorations.configure({ getPhases: () => currentPhases }),
            BlameDecorations.configure({
                getEnabled: () => currentBlame,
                getHistory: () => currentHistory,
            }),
            RevealDecorations.configure({ getPhases: () => currentPhases }),
            AgentRibbon.configure({ getSteps: () => currentSteps, getRole: () => currentRole }),
            HoldDecorations.configure({
                getHeld: () => currentHeld,
                getDetail: () => currentHoldDetail,
                onWithdraw: opts.onWithdrawRealization,
                getSessionLive: () => currentSessionLive,
            }),
            BlockDecorations.configure({
                getBlocks: () => currentBlocks,
                onEdit: opts.onBlockEdit,
            }),
            BlockSuggestion.configure({
                getActiveFid: () => activeFid(),
                onCreate: edit => opts.onBlockEdit?.(edit),
                char: '/',
            }),
            CapturedDecorations.configure({
                // Phase 1 of the lifecycle: every user edit gets the "recorded, not sent"
                // mark — client-side, so it never waits on the daemon's classification.
                getBaseline: () => capturedBaseline,
                getDrafts: () => currentDrafts,
                getHandedOff: () => currentHeld, // handed-off features show pending, not captured
            }),
            GlanceDecorations.configure({
                isGlance: () => glanceOn,
                getPitch: (fid: string) => currentPitches[fid] ?? '',
            }),
            makeKeymap(() => commitNow()),
        ],
        content: { type: 'doc', content: [{ type: 'paragraph' }] },
        autofocus: false,
        onUpdate: () => {
            if (suppressUpdate) return;
            dirty = true;
            lastTypedAt = Date.now();
            scheduleSettle();
            scheduleRail();
            if (currentComments.length || composer) scheduleCommentReflow(); // anchors shift as you type
            // P2 doc→code bridge: the live edit's owning feature — the webview debounces
            // this and opens that feature's bound code Beside (§A.1).
            const editedFid = activeFid();
            // U5 version gate: this feature now has an un-acked local edit, so a returning
            // projection must not clobber it unless its per-feature version is newer.
            if (editedFid) pendingFids.add(editedFid);
            opts.onEditFeature?.(editedFid);
            // P2 fix 2: editing the feature means the user is now reviewing it → clear its
            // code-touched tick (it has served its purpose).
            if (editedFid) clearTouchTick(editedFid);
        },
        onSelectionUpdate: () => {
            const { from, to, empty } = editor.state.selection;
            if (!empty && to > from) lastSelection = { from, to };
            updateBubble();
            if (opts.onActiveFeature) opts.onActiveFeature(activeFid(), 'selection');
        },
    });

    /** The range a selection-driven toolbar/bubble action should target: the live
     *  selection if non-empty, else the last non-empty one — clamped to the current
     *  doc so a stale fallback (after a reload) can't address out-of-bounds positions.
     *  Returns null when there's nothing valid to act on. */
    function actionRange(): { from: number; to: number } | null {
        const sel = editor.state.selection;
        const cand = (!sel.empty && sel.to > sel.from) ? { from: sel.from, to: sel.to } : lastSelection;
        if (!cand) return null;
        const max = editor.state.doc.content.size;
        const from = Math.max(0, Math.min(cand.from, max));
        const to = Math.max(0, Math.min(cand.to, max));
        return to > from ? { from, to } : null;
    }

    function activeFid(): string | null {
        const { $from } = editor.state.selection;
        for (let d = $from.depth; d >= 0; d--) {
            const node = $from.node(d);
            if (node.type.name === 'featureHeading') return (node.attrs.fid as string) ?? null;
        }
        if ($from.depth < 1) return null; // doc-level / node selection — no owning heading
        // Selection in a description: walk back to the owning heading.
        let fid: string | null = null;
        const here = $from.before(1);
        editor.state.doc.forEach((node, offset) => {
            if (node.type.name === 'featureHeading' && offset <= here) fid = (node.attrs.fid as string) ?? null;
        });
        return fid;
    }

    /** Index of the heading the caret is in (among headings) — a fid-independent
     *  anchor so a brand-new feature (fid still null until minted) can keep its caret
     *  across the mint reload. */
    function activeHeadingIndex(): number {
        const { $from } = editor.state.selection;
        const here = $from.depth >= 1 ? $from.before(1) : 0;
        let idx = -1;
        let seen = -1;
        editor.state.doc.forEach((node, offset) => {
            if (node.type.name !== 'featureHeading') return;
            seen++;
            if (offset <= here) idx = seen;
        });
        return idx;
    }

    function headingPosAtIndex(index: number): number | null {
        let seen = -1;
        let pos: number | null = null;
        editor.state.doc.forEach((node, offset) => {
            if (node.type.name !== 'featureHeading') return;
            seen++;
            if (seen === index && pos === null) pos = offset;
        });
        return pos;
    }

    /** Signature of the agent code-ahead AMENDs (the proposals the host materializes
     *  as engine marks in the payload doc). Changes when a proposal appears, mutates,
     *  or resolves — the setDoc reload trigger keys on it so the marks render/clear
     *  even when the baseline `tree.codoc` text is unchanged (notably on reject). */
    function proposalsSig(): string {
        return currentSuggestions
            .filter(s => s.direction === 'code-ahead' && s.kind === 'amend')
            .map(s => `${s.id}:${s.titleNew ?? ''}:${s.descNew ?? ''}`)
            .join('|');
    }

    function scheduleSettle(): void {
        if (settleTimer) clearTimeout(settleTimer);
        settleTimer = window.setTimeout(settleNow, SETTLE_DEBOUNCE_MS);
        markSaving('saving…');
    }

    /** True when the caret sits inside a feature heading — a title mid-authorship. */
    function caretInHeading(): boolean {
        try {
            return editor.state.selection.$head.parent.type.name === 'featureHeading';
        } catch {
            return false;
        }
    }

    function settleNow(force = false): void {
        if (settleTimer) { clearTimeout(settleTimer); settleTimer = 0; }
        if (!dirty) return;
        // Never ship a half-composed word. The trailing debounce (or a
        // pagehide/visibility flush) can fire while an IME composition is open,
        // and the doc then holds raw composition text the author has not
        // committed — settling it would mint a permanent, id-stamped edit from
        // garbled input. Reschedule instead; the compositionend transaction
        // re-enters the ordinary debounce path. The incoming direction already
        // defers the same way (DeferConditions.imeComposing).
        if (editor.view.composing) { scheduleSettle(); return; }
        // Never ship a half-typed TITLE either. A pause mid-title used to settle
        // whatever fragment existed — observed live as set_title commands "D" →
        // "Dra" → "Draf" and an "Untitled" add for a bare `## ` — junk the daemon
        // then applied, re-projected, and (before the adopt gate) yanked the
        // caret with. The title settles when the caret LEAVES the heading (the
        // pending debounce re-fires and passes this guard), or immediately on
        // blur/commit/adopt (`force`), where the author is genuinely done.
        if (!force && editor.view.hasFocus() && caretInHeading()) {
            scheduleSettle();
            return;
        }
        dirty = false;
        // Anything now back to the text it last adopted has nothing left to protect,
        // so it stops being pending. Without this an undo leaves the feature pending
        // against text identical to the daemon's, and — since the daemon never
        // advanced its version — no future projection is ever "strictly newer" and
        // the gate refuses every later update to it.
        const stillPending = settledPendingFids(
            pendingFids, featureBlocks(editor.getJSON() as PMNode), adoptedText,
        );
        pendingFids.clear();
        for (const fid of stillPending) pendingFids.add(fid);
        // ONE edit path (U3): the human's edit always COMMITS. The daemon classifies
        // it (pure-doc vs code-implying) and, when it implies code, lands the feature
        // in the hold set → the calm "being realized" badge surfaces back. No
        // client-side suggest/strip/dual-state.
        opts.onSettle(editor.getJSON() as PMNode, adoptedBaselineId);
        markSaving('saved');
        rebuildRail();
    }

    /** Stage & SEND (U4): the explicit Save/Commit gesture. Cancels the pending debounce
     *  and hands the WHOLE current doc to the host in one shot — the host persists it (so
     *  the latest keystroke isn't lost) and hands the staged code-implying edits to the
     *  agent. A no-op-ish call when nothing is staged (the host's settle short-circuits and
     *  hand-off clears an empty draft set). */
    function commitNow(): void {
        if (settleTimer) { clearTimeout(settleTimer); settleTimer = 0; }
        dirty = false;
        const doc = editor.getJSON() as PMNode;
        // Re-baseline to the committed text: a prose edit's captured marks clear now, and a
        // code edit graduates to pending once the host's repost marks it handed-off. This is
        // the SECOND re-baseline point (the other is a real reload in setDoc).
        capturedBaseline = featureBlocks(doc);
        editor.view.dispatch(editor.state.tr.setMeta(CAPTURED_UPDATED, true));
        opts.onCommit?.(doc, adoptedBaselineId);
        markSaving('sent');
    }

    // ── toolbar: marks + structure ────────────────────────────────────────────
    const marks = document.createElement('div');
    marks.className = 'ce-marks';
    marks.append(
        iconButton('B', 'Bold (⌘B)', () => editor.chain().focus().toggleBold().run(), 'ce-bold'),
        iconButton('I', 'Italic (⌘I)', () => editor.chain().focus().toggleItalic().run(), 'ce-italic'),
        iconButton('H', 'Highlight', () => editor.chain().focus().toggleHighlight().run(), 'ce-hl'),
        iconButton('❝', 'Comment on the selection — a steering note the agent will address', () => openComposerForSelection(), 'ce-cm'),
    );

    const structure = document.createElement('div');
    structure.className = 'ce-structure';
    structure.append(
        iconButton('＋ feature', 'New feature (sibling)', () => { newFeatureHeading(editor); }, 'ce-new'),
        // indent / outdent are Tab / Shift-Tab (makeKeymap) — no redundant toolbar buttons (U5).
        iconButton('~ retire', 'Toggle retire on this feature', () => { toggleRetireHeading(editor); editor.commands.focus(); }, 'ce-retire'),
        // Create a NEW feature as a plan/build request (realized=false) — born plan, so
        // committing it asks the agent to build the feature. This is the typed "build
        // this" gesture that replaced the deleted is_imperative prose guess.
        iconButton('◇ plan', 'New build-request feature (plan — the agent implements it on commit)',
            () => { newFeatureHeading(editor, { realized: false }); }, 'ce-plan'),
    );

    const spacer = document.createElement('div');
    spacer.className = 'ce-spacer';
    const saveState = document.createElement('span');
    saveState.className = 'ce-savestate';
    function markSaving(text: string): void { saveState.textContent = text; }

    // ONE editing surface: no Editing/Suggesting toggle, no pen/pencil instrument.
    toolbar.append(marks, structure, spacer, saveState);
    wrap.append(toolbar, body);
    container.append(wrap);

    // ── TOC rail + scroll-spy (rehomed scroll indicator) ──────────────────────
    const tickByFid = new Map<string, HTMLElement>();
    const tickList: HTMLElement[] = [];   // ticks in document order — the wave band (U4)
    let lastWaveIndex = -1;               // dedupe the wave to once per hovered tick
    function headingDom(pos: number): HTMLElement | null {
        const dom = editor.view.nodeDOM(pos) as Node | null;
        return dom && dom.nodeType === 1 ? (dom as HTMLElement) : (dom?.parentElement ?? null);
    }
    function markCurrent(fid: string | null): void {
        surface.querySelectorAll('.codoc-feature-heading.ce-current').forEach(e => e.classList.remove('ce-current'));
        for (const [f, tick] of tickByFid) tick.classList.toggle('active', f === fid);
        if (!fid) return;
        const pos = headingPosForFid(editor, fid);
        if (pos != null) headingDom(pos)?.classList.add('ce-current');
    }

    // Code→doc spark (P2 / §A.3): per-fid timers so a re-touch re-arms cleanly rather than
    // stacking glyphs. The glyph rises (sparkIn), holds ~2.5 s, then the heading keeps a quiet
    // blue underline tick (.ce-code-touched).
    const touchTimers = new Map<string, number>();   // spark hold → remove the inbound glyph
    const tickTimers = new Map<string, number>();    // P2 fix 2: bounded tick lifetime → clear the class
    const TOUCH_HOLD_MS = 2500;
    const TICK_LIFETIME_MS = 8000;   // the blue tick must not haunt the heading forever
    function headingDomForFid(fid: string): HTMLElement | null {
        return surface.querySelector<HTMLElement>(`.codoc-feature-heading[data-fid="${CSS.escape(fid)}"]`);
    }
    /** Deterministically clear the code-touched tick on a feature (P2 fix 2) — doesn't depend on
     *  a full setDoc rebuild (which early-returns while the user is editing). */
    function clearTouchTick(fid: string): void {
        const t = tickTimers.get(fid); if (t) { clearTimeout(t); tickTimers.delete(fid); }
        const dom = headingDomForFid(fid);
        dom?.classList.remove('ce-code-touched', 'ce-code-touched-big');
        dom?.querySelector('.ce-code-touch')?.remove();
    }
    function touchFeaturesInternal(fids: string[], big?: Set<string>): void {
        for (const fid of fids) {
            const dom = headingDomForFid(fid);
            if (!dom) continue;
            dom.classList.add('ce-code-touched');
            if (big?.has(fid)) dom.classList.add('ce-code-touched-big'); // a large change → divergent-grade emphasis (§A.3)
            // one inbound glyph per heading (drop a stale one first so re-touch doesn't stack)
            dom.querySelector('.ce-code-touch')?.remove();
            const spark = document.createElement('span');
            spark.className = 'ce-code-touch';
            spark.contentEditable = 'false';
            spark.title = 'A code edit touched this feature — review on the next sync.';
            spark.append(icon('arrow-bend-down-left'));
            dom.append(spark);
            sparkIn(spark);
            const prev = touchTimers.get(fid);
            if (prev) clearTimeout(prev);
            touchTimers.set(fid, window.setTimeout(() => {
                touchTimers.delete(fid);
                headingDomForFid(fid)?.querySelector('.ce-code-touch')?.remove(); // settle to the tick
            }, TOUCH_HOLD_MS));
            // P2 fix 2: the tick itself has a BOUNDED lifetime — re-armed on each touch — so a
            // stale blue underline can never accumulate even if no setDoc rebuild ever fires.
            const prevTick = tickTimers.get(fid);
            if (prevTick) clearTimeout(prevTick);
            tickTimers.set(fid, window.setTimeout(() => clearTouchTick(fid), TICK_LIFETIME_MS));
        }
    }

    let muteSpy = false;
    let muteTimer = 0;
    let navTween: TweenController | null = null;
    // Land the heading just below the scroll-spy's active threshold (surface top + 72), so the
    // section we glide to is the one the spy then marks active.
    const SPY_TOP_INSET = 72;
    function scrollToFeatureInternal(fid: string, smooth: boolean): void {
        const pos = headingPosForFid(editor, fid);
        if (pos == null) return;
        const dom = headingDom(pos);
        if (!dom) return;
        // Tween the SURFACE's scrollTop (the real scroll container — it owns the spy listener),
        // not scrollIntoView, so the motion has momentum and we control the landing offset.
        const surfTop = surface.getBoundingClientRect().top;
        const target = Math.max(0, surface.scrollTop + (dom.getBoundingClientRect().top - surfTop) - SPY_TOP_INSET + 1);
        const distance = Math.abs(target - surface.scrollTop);
        const animated = smooth && !prefersReducedMotion();
        const duration = animated ? navDuration(distance) : 0;
        // The navigation IS the selection — set it directly and mute the spy for the WHOLE glide
        // so intermediate scroll positions don't flicker-select a neighbour. A second nav cancels
        // the in-flight tween rather than queuing.
        markCurrent(fid);
        if (navTween) { navTween.cancel(); navTween = null; }
        muteSpy = true;
        clearTimeout(muteTimer);
        if (animated) {
            navTween = tweenScrollTop(surface, target, {
                duration, ease: 'outExpo',
                // clear muteSpy in the tween's OWN completion (the timer below is only a floor) —
                // anime.js pause() doesn't fire onComplete, so the wheel-cancel path clears it too.
                onComplete: () => { navTween = null; muteSpy = false; },
            });
            muteTimer = window.setTimeout(() => { muteSpy = false; }, muteWindowFor(duration));
        } else {
            surface.scrollTop = Math.round(target);
            muteTimer = window.setTimeout(() => { muteSpy = false; }, 350);
        }
    }
    /** The rail tick's state for a feature — the SAME ordered projection the row
     *  badge uses (feature-state.railState), fed from this editor's live signal
     *  maps, so the minimap and the row can never tell different stories. */
    function railSignalsFor(fid: string, attrs: Record<string, unknown>): RailSignals {
        const phase = currentPhases[fid];
        const hasProposal = currentSuggestions.some(s =>
            s.featureId === fid && (s.kind === 'amend' || s.kind === 'retire' || s.kind === 'move'));
        return {
            busy: currentBusy.has(fid),
            activeMode: phase === 'editing' ? 'write' : phase === 'reflecting' ? 'read' : null,
            proposalOp: hasProposal ? 'amend' : null,
            autoEdit: !!currentAutoEdits[fid],
            sent: currentHeld.has(fid),
            staged: currentDrafts.has(fid),
            realized: attrs.realized === false ? false : true,
            retired: !!attrs.retired,
        };
    }

    function rebuildRail(): void {
        rail.replaceChildren();
        tickByFid.clear();
        tickList.length = 0;
        lastWaveIndex = -1; // fresh tick DOM → re-arm the wave (else a rebuild mid-hover dead-zones it)
        editor.state.doc.forEach(node => {
            if (node.type.name !== 'featureHeading') return;
            const fid = node.attrs.fid as string | null;
            if (!fid) return;
            const tick = document.createElement('div');
            const st = railState(railSignalsFor(fid, node.attrs as Record<string, unknown>));
            // One class per state — the minimap IS the status strip, in the same
            // colour encoding the in-document decorations already use (CSS keys the
            // hue off `st-*`; `settled` draws the quiet neutral tick).
            tick.className = 'ce-tick st-' + st;
            tick.style.setProperty('--d', String(Math.min(Number(node.attrs.level) || 0, 4)));
            if (node.attrs.retired) tick.classList.add('retired');
            if (node.attrs.realized === false) tick.classList.add('unrealized');
            const name = node.textContent || '(untitled)';
            tick.title = st === 'settled' ? name : `${name} — ${RAIL_STATE_LABEL[st]}`;
            tick.addEventListener('click', () => scrollToFeatureInternal(fid, true));
            tickByFid.set(fid, tick);
            tickList.push(tick);
            rail.append(tick);
        });
        // The legend: pinned under the ticks, only when any tick carries a state —
        // a legend over an all-quiet rail would be noise explaining nothing.
        const states = new Set(tickList.map(t => (t.className.match(/st-([a-z]+)/) ?? [])[1]));
        states.delete('settled');
        if (states.size) {
            const legend = document.createElement('button');
            legend.type = 'button';
            legend.className = 'ce-rail-legend';
            legend.textContent = '?';
            legend.title = [...states]
                .map(s => `● ${s}: ${RAIL_STATE_LABEL[s as keyof typeof RAIL_STATE_LABEL]}`)
                .join('\n');
            legend.setAttribute('aria-label', 'Minimap legend');
            rail.append(legend);
        }
        updateSpy();
    }
    // Wave hover (U4): sweeping the cursor down the rail ripples a horizontal scaleX out from
    // the hovered tick (±3 neighbours, falloff), settling back together on leave. Listeners
    // live on the stable `rail` (rebuildRail only swaps its children), deduped on the hovered
    // index so the wave re-fires once per tick, not per pixel. Reduced motion → no wave (the
    // CSS :hover gives the single-tick feedback). Peak scales per index-distance from centre.
    const WAVE_PEAKS = [1.6, 1.35, 1.18, 1.06];
    rail.addEventListener('mouseover', ev => {
        const tick = (ev.target as HTMLElement).closest('.ce-tick') as HTMLElement | null;
        if (!tick) return;
        const idx = tickList.indexOf(tick);
        if (idx < 0 || idx === lastWaveIndex) return;
        lastWaveIndex = idx;
        staggerHover(tickList, idx, 'scaleX', d => WAVE_PEAKS[d] ?? 1, { radius: 3, step: 30, duration: 120 });
    });
    rail.addEventListener('mouseleave', () => {
        if (lastWaveIndex < 0) return;
        lastWaveIndex = -1;
        staggerHover(tickList, 0, 'scaleX', () => 1, { radius: tickList.length, step: 0, duration: 140 });
    });
    let spyRaf = 0;
    let lastSpyFid: string | null = null; // dedupe scroll-spy onActiveFeature notifications
    function updateSpy(): void {
        if (spyRaf) return;
        spyRaf = requestAnimationFrame(() => {
            spyRaf = 0;
            if (muteSpy) return; // a programmatic scroll is in flight — don't fight it
            const surfRect = surface.getBoundingClientRect();
            const threshold = surfRect.top + 72;
            let current: string | null = null;
            // Viewport band: a tick lights while ANY of its section [its heading →
            // the next heading] is on screen. The contiguous lit run IS the viewport
            // indicator — the minimap says where you are, not just what each section
            // is doing. Two passes: collect heading tops, then mark intersections.
            const tops: { fid: string; top: number }[] = [];
            editor.state.doc.forEach((node, pos) => {
                if (node.type.name !== 'featureHeading') return;
                const fid = node.attrs.fid as string | null;
                if (!fid) return;
                const dom = headingDom(pos);
                if (!dom) return;
                const top = dom.getBoundingClientRect().top;
                if (top <= threshold) current = fid;
                tops.push({ fid, top });
            });
            tops.forEach((h, i) => {
                const sectionEnd = tops[i + 1]?.top ?? Number.POSITIVE_INFINITY;
                const inView = h.top <= surfRect.bottom && sectionEnd >= surfRect.top;
                tickByFid.get(h.fid)?.classList.toggle('in-view', inView);
            });
            markCurrent(current);
            // Only notify on an actual section change — scroll-spy runs every RAF frame,
            // and onActiveFeature drives the tree highlight + dependency spotlight recompute
            // (an O(rows) DOM pass), so firing it per-frame thrashes a large tree.
            if (current && current !== lastSpyFid) {
                lastSpyFid = current;
                opts.onActiveFeature?.(current, 'scroll');
            }
        });
    }

    // (The v6 dwell-to-acknowledge timer is retired: an unasked rewrite now clears
    // only on an explicit Keep/Restore verdict — auto-edit-decorations.ts — because
    // a record that evaporates the moment you look at it cannot be disagreed with.)
    let railTimer = 0;
    function scheduleRail(): void {
        if (railTimer) clearTimeout(railTimer);
        railTimer = window.setTimeout(rebuildRail, 250);
    }
    surface.addEventListener('scroll', updateSpy, { passive: true });
    // A user wheel during a momentum glide cancels it immediately so the tween doesn't fight the
    // user — the 'scroll' handler (updateSpy) is muted during the glide and can't do this itself.
    // Mirrors the tree pane's onTreeWheel; after cancel the spy un-mutes so the active feature tracks.
    surface.addEventListener('wheel', () => {
        if (!navTween) return;
        navTween.cancel(); navTween = null;
        muteSpy = false;
        if (muteTimer) { clearTimeout(muteTimer); muteTimer = 0; }
    }, { passive: true });

    // Code-ref chip click → navigate.
    editor.view.dom.addEventListener('click', ev => {
        const chip = (ev.target as HTMLElement).closest('.codoc-code-ref') as HTMLElement | null;
        if (!chip) return;
        ev.preventDefault();
        opts.onOpenBinding(chip.getAttribute('data-file') || '', chip.getAttribute('data-symbol') || '');
    });

    // Hovering a depends-on / used-by link previews its target in the tree pane (WS5):
    // highlight + scroll-to without changing the caret. Delegated so it survives deco
    // re-renders; deduped on fid so it fires once per link, not per pixel.
    let hoverFid: string | null = null;
    const emitHover = (fid: string | null): void => {
        if (fid === hoverFid) return;
        hoverFid = fid;
        opts.onHoverFeature?.(fid);
    };
    editor.view.dom.addEventListener('mouseover', ev => {
        const link = (ev.target as HTMLElement).closest('.ce-thread[data-fid]') as HTMLElement | null;
        emitHover(link?.dataset.fid ?? null);
    });
    editor.view.dom.addEventListener('mouseout', ev => {
        const to = (ev as MouseEvent).relatedTarget as HTMLElement | null;
        if (!to || !to.closest('.ce-thread[data-fid]')) emitHover(null);
    });

    // Tier-1 hover-preview cards (U4): hover/keyboard a codeRef chip or a feature
    // dependency link → a transient card from the precomputed payload data. Pure
    // overlay; never touches the doc. Torn down in destroy().
    const detachHoverCards = attachHoverCards(editor.view.dom as HTMLElement, {
        getCards: () => currentHoverCards,
        onOpenBinding: opts.onOpenBinding,
        onNavigate: fid => scrollToFeatureInternal(fid, true),
    });

    /** Patch freshly-minted feature ids into the live editor even while the user is
     *  still editing (dirty). Without this, a new heading stays fid:null in the editor,
     *  so the next settle re-emits a fid-less heading and the pipeline ADDs a SECOND
     *  feature.
     *
     *  Robust matching (the old by-raw-index pairing broke on any concurrent indent /
     *  reorder / sibling-add between settle and repost): only headings whose fid is NOT
     *  already present locally count as freshly minted; each local fid:null heading
     *  claims a minted id by TITLE first (so a uniquely-named new feature always lands
     *  its own id), then falls back to document order. Never reuses a minted id; only
     *  ever FILLS a null. */
    function patchMintedIds(incoming: PMNode): void {
        const localFids = new Set<string>();
        editor.state.doc.forEach(node => {
            if (node.type.name === 'featureHeading' && node.attrs.fid) localFids.add(node.attrs.fid as string);
        });
        // Title/order candidates from the incoming doc — the LEGACY fallback, used only
        // for null-fid nodes that have no localId match (e.g. a raw-text-editor add).
        const minted: { title: string; fid: string }[] = [];
        for (const b of incoming.content ?? []) {
            if (b.type !== 'featureHeading') continue;
            const fid = (b.attrs as { fid?: string | null } | undefined)?.fid;
            if (fid && !localFids.has(fid)) minted.push({ title: inlineRunsToText(b.content).trim(), fid });
        }

        const used = new Set<number>();
        let tr = editor.state.tr;
        let changed = false;
        editor.state.doc.forEach((node, pos) => {
            if (node.type.name !== 'featureHeading' || node.attrs.fid != null) return;
            // v6: EXACT match by localId → fid (the daemon persisted the localId on the
            // minted feature). No title/order guessing, so a node with an empty/edited
            // title or a shifted position still reconciles to the right fid — killing the
            // duplicate/orphan "Untitled" + "new node" churn and the caret jump.
            const lid = node.attrs.localId as string | null;
            const exact = lid ? currentMintedByLocalId[lid] : undefined;
            if (exact && !localFids.has(exact)) {
                tr = tr.setNodeMarkup(pos, undefined, { ...node.attrs, fid: exact });
                localFids.add(exact);
                changed = true;
                return;
            }
            // Legacy fallback (no localId match): title then order.
            const title = (node.textContent || '').trim();
            let pick = minted.findIndex((m, i) => !used.has(i) && m.title === title);
            if (pick < 0) pick = minted.findIndex((_m, i) => !used.has(i));
            if (pick < 0) return;
            used.add(pick);
            tr = tr.setNodeMarkup(pos, undefined, { ...node.attrs, fid: minted[pick].fid });
            changed = true;
        });
        if (!changed) return;
        suppressUpdate = true;
        editor.view.dispatch(tr.setMeta(REFLECT_META, true).setMeta('addToHistory', false));
        suppressUpdate = false;
    }

    // ── inline comments — selection toolbar + composer + resolve ──────────────
    // Selecting prose surfaces a Notion/Medium-style floating toolbar (format · comment ·
    // hand-to-AI). Comment opens a composer aside the selection; Enter saves: a `comment`
    // mark anchors the threadId, the note is handed to the host, which forwards it to Loop B
    // as a one-shot STEER on edits.json (U2b — the host no longer writes tree.codoc). The
    // marker icon + popover live in comment-decorations.ts; resolving removes the anchor
    // mark here and tells the host to drop the thread.
    type ComposeMode = 'create' | 'edit';
    const bubble = document.createElement('div');
    bubble.className = 'ce-bubble';
    bubble.style.display = 'none';
    // Guard the WHOLE bubble (not just the buttons): a mousedown landing on the bubble's
    // padding would otherwise blur the editor and collapse the selection before a
    // button's click fires — the "selection vanished, nothing happened" bug.
    bubble.addEventListener('mousedown', ev => ev.preventDefault());
    function bubBtn(label: string, title: string, onClick: () => void, cls = ''): HTMLButtonElement {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = ('ce-bub-btn ' + cls).trim();
        b.textContent = label;
        b.title = title;
        b.addEventListener('mousedown', ev => ev.preventDefault()); // keep the editor selection
        b.addEventListener('click', ev => { ev.preventDefault(); onClick(); });
        return b;
    }
    const bubSep = (): HTMLElement => { const s = document.createElement('span'); s.className = 'ce-bub-sep'; return s; };
    bubble.append(
        bubBtn('B', 'Bold (⌘B)', () => editor.chain().focus().toggleBold().run(), 'ce-bub-bold'),
        bubBtn('I', 'Italic (⌘I)', () => editor.chain().focus().toggleItalic().run(), 'ce-bub-italic'),
        bubBtn('H', 'Highlight', () => editor.chain().focus().toggleHighlight().run(), 'ce-bub-hl'),
        bubSep(),
        bubBtn('❝ Comment', 'Comment on the selection — a steering note the agent will address', () => openComposerForSelection()),
    );
    document.body.append(bubble);

    let composer: HTMLElement | null = null;
    let composeMode: ComposeMode = 'create';
    let composeRange: { from: number; to: number } | null = null;
    // W5 fix: a projection that arrives while a comment composer / selection bubble
    // is open — or while an IME composition is in flight — is DEFERRED here (not
    // dropped), then re-applied the moment that clears. Only the LATEST is kept.
    // Carries its baselineId with it: a deferred projection must not be adopted under a
    // citation that has since moved on (the id and the doc are one fact, not two).
    let pendingProjection: { doc: PMNode; baselineId?: number } | null = null;
    // How long after the last keystroke the author still counts as mid-thought.
    // Just above the settle debounce, so "typing with natural pauses" never adopts.
    const RECENT_TYPING_MS = 1500;
    const isComposing = (): boolean => shouldDeferProjection({
        composerOpen: !!composer,
        bubbleOpen: bubble.style.display !== 'none',
        imeComposing: !!editor.view?.composing,
        activelyEditing: !!editor.view?.hasFocus()
            && (dirty || Date.now() - lastTypedAt < RECENT_TYPING_MS),
    });
    let projectionFlushTimer = 0;
    function scheduleProjectionFlush(): void {
        if (projectionFlushTimer) clearTimeout(projectionFlushTimer);
        projectionFlushTimer = window.setTimeout(() => {
            projectionFlushTimer = 0;
            flushPendingProjection();
        }, RECENT_TYPING_MS + 200);
    }
    function flushPendingProjection(): void {
        if (!pendingProjection) return;
        // Still blocked (typing resumed, composer reopened, IME) → keep the LATEST
        // projection parked and try again shortly; the doc must never sit stale
        // forever waiting on a flush nobody schedules.
        if (isComposing()) { scheduleProjectionFlush(); return; }
        const p = pendingProjection;
        pendingProjection = null;
        handle.setDoc(p.doc, p.baselineId);
    }
    // Leaving the editor is the natural adoption point: the author is done
    // mid-thought, so settle whatever is unsent and let the parked projection in.
    surface.addEventListener('focusout', () => setTimeout(() => {
        if (dirty) settleNow(true);
        flushPendingProjection();
    }, 0));
    // ProseMirror finishes its own composition bookkeeping after this event, so the
    // deferred projection lands on the next tick — applying it inline would replace
    // the document while the view is still resolving the composition.
    surface.addEventListener('compositionend', () => setTimeout(flushPendingProjection, 0));
    let composeFid: string | null = null;
    let composeAnchor = '';
    let composeThreadId = '';
    // A TRANSIENT screenshot attachment (U6) captured in the composer, sent with the
    // comment on save and discarded after. Base64 bytes — the host stores them.
    let composeMedia: { data: string; mime: string; name: string } | null = null;


    function updateBubble(): void {
        if (composer) { bubble.style.display = 'none'; return; }
        const { from, to, empty } = editor.state.selection;
        const rect = empty ? null : coordsRect(from, to);
        // Only over real prose: both ends inside a paragraph/heading text block.
        const inText = !empty && editor.state.doc.resolve(from).parent.isTextblock
            && to - from >= 1;
        if (!rect || !inText) { bubble.style.display = 'none'; return; }
        bubble.style.display = 'block';
        const bw = bubble.offsetWidth || 96;
        bubble.style.left = `${Math.max(8, Math.min(rect.left + rect.width / 2 - bw / 2, window.innerWidth - bw - 8))}px`;
        bubble.style.top = `${Math.max(8, rect.top - bubble.offsetHeight - 8)}px`;
    }
    function closeBubble(): void { bubble.style.display = 'none'; flushPendingProjection(); }

    function selectedText(from: number, to: number): string {
        return editor.state.doc.textBetween(from, to, ' ', ' ');
    }

    function openComposerForSelection(): void {
        const range = actionRange();
        if (!range) { editor.commands.focus(); return; }
        const { from, to } = range;
        composeMode = 'create';
        composeRange = { from, to };
        composeFid = activeFid();
        composeAnchor = selectedText(from, to);
        composeThreadId = mintCommentId(Date.now(), String(from));
        composeMedia = null;
        // The composer opens in the right margin, aligned to the captured range (which
        // survives a collapsed selection), so it always sits beside the text it comments on.
        openComposer('');
    }

    function openComposerForEdit(thread: CommentThread): void {
        composeMode = 'edit';
        composeThreadId = thread.id;
        composeRange = commentMarkRange(thread.id);
        composeFid = thread.featureId;
        composeAnchor = thread.anchorText;
        openComposer(thread.body);
    }

    /** Bounding rect of the doc range [from,to) — the anchor for the bubble + composer.
     *  Uses min/max of both endpoints so a multi-line selection still yields a sane box
     *  (the prior `b.right - a.left` width went haywire across a line break). */
    function coordsRect(from: number, to: number): DOMRect | null {
        try {
            const a = editor.view.coordsAtPos(from);
            const b = editor.view.coordsAtPos(to);
            const left = Math.min(a.left, b.left);
            const top = Math.min(a.top, b.top);
            return new DOMRect(left, top, Math.max(a.right, b.right) - left, Math.max(a.bottom, b.bottom) - top);
        } catch { return null; }
    }

    function openComposer(initial: string): void {
        closeComposer();
        closeBubble();
        const box = document.createElement('div');
        box.className = 'ce-cmt-composer';
        if (composeAnchor.trim()) {
            const ctx = document.createElement('div');
            ctx.className = 'ce-cmt-ctx';
            ctx.textContent = composeAnchor;
            ctx.title = composeAnchor;
            box.append(ctx);
        }
        const ta = document.createElement('textarea');
        ta.className = 'ce-cmt-input';
        ta.rows = 2;
        ta.placeholder = composeMode === 'edit' ? 'Edit your note…' : 'Tell the agent what to change…';
        ta.value = initial;
        ta.addEventListener('keydown', ev => {
            if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); saveComposer(ta.value); }
            else if (ev.key === 'Escape') { ev.preventDefault(); closeComposer(); editor.commands.focus(); }
        });
        const foot = document.createElement('div');
        foot.className = 'ce-cmt-composer-foot';
        const hint = document.createElement('span');
        hint.className = 'ce-cmt-hint';
        hint.textContent = '⏎ send · esc cancel';
        // Attach a transient screenshot (U6) — create mode only (an edit re-hands the
        // note text, not the one-shot attachment). The chosen image rides the steer
        // as consult media and is consumed once by realization.
        if (composeMode !== 'edit') {
            const file = document.createElement('input');
            file.type = 'file';
            file.accept = 'image/*';
            file.className = 'ce-cmt-shot-input';
            file.style.display = 'none';
            const clip = document.createElement('button');
            clip.type = 'button';
            clip.className = 'ce-cmt-shot';
            clip.title = 'Attach a screenshot — the agent consults it before implementing';
            const paint = () => { clip.textContent = composeMedia ? `📎 ${composeMedia.name}` : '📎'; };
            paint();
            clip.addEventListener('mousedown', ev => ev.preventDefault());
            clip.addEventListener('click', ev => { ev.preventDefault(); file.click(); });
            file.addEventListener('change', () => {
                const f = file.files && file.files[0];
                if (!f) return;
                const reader = new FileReader();
                reader.onload = () => {
                    const res = String(reader.result || '');
                    const comma = res.indexOf(',');
                    composeMedia = { data: comma >= 0 ? res.slice(comma + 1) : res, mime: f.type || 'image/png', name: f.name || 'screenshot' };
                    paint();
                };
                reader.readAsDataURL(f);
            });
            box.append(file);
            foot.append(clip);
        }
        const send = document.createElement('button');
        send.type = 'button';
        send.className = 'ce-cmt-send';
        send.textContent = composeMode === 'edit' ? 'Save' : 'Send';
        send.addEventListener('mousedown', ev => ev.preventDefault());
        send.addEventListener('click', ev => { ev.preventDefault(); saveComposer(ta.value); });
        foot.append(hint, send);
        box.append(ta, foot);
        box.classList.add('in-margin');
        surface.appendChild(box);   // inside the scroll surface → scrolls with the anchored text
        composer = box;
        syncCommentsPresence();     // open the margin (shift the prose left to make room)
        positionComposerInMargin(); // align the composer to the anchored text's vertical offset
        renderCommentMargin();      // re-place any existing thread cards beside it
        ta.focus();
        // dismiss on outside click
        setTimeout(() => document.addEventListener('mousedown', onComposerOutside, true), 0);
    }
    function onComposerOutside(e: MouseEvent): void {
        if (composer && !composer.contains(e.target as Node)) { closeComposer(); }
    }
    function closeComposer(): void {
        document.removeEventListener('mousedown', onComposerOutside, true);
        composer?.remove();
        composer = null;
        syncCommentsPresence();   // close the margin if no threads remain (prose slides back)
        renderCommentMargin();    // re-place cards now that the prose width changed
        flushPendingProjection(); // W5: apply any projection deferred while composing
    }

    function commentMarkRange(threadId: string): { from: number; to: number } | null {
        let found: { from: number; to: number } | null = null;
        editor.state.doc.descendants((node, pos) => {
            if (!node.isText || !node.marks.length) return;
            const cm = node.marks.find(m => m.type.name === 'comment');
            if ((cm?.attrs.threadId as string | undefined) !== threadId) return;
            const from = pos, to = pos + node.nodeSize;
            if (!found) found = { from, to };
            else if (found.to === from) found.to = to;
        });
        return found;
    }

    /** Apply a comment-mark mutation WITHOUT tripping the settle machinery. A bare
     *  dispatch would set dirty + schedule a settle (and clearing dirty afterwards
     *  would drop any UNRELATED pending edit). suppressUpdate makes onUpdate ignore
     *  this transaction, so dirty and any in-flight settle are left exactly as they
     *  were; the host persists the mark via the comment-* message, not the settle. */
    function commentMutate(mutate: (tr: import('@tiptap/pm/state').Transaction) => void): void {
        suppressUpdate = true;
        try { const tr = editor.state.tr; mutate(tr); editor.view.dispatch(tr); }
        finally { suppressUpdate = false; }
    }

    function saveComposer(value: string): void {
        const body = value.trim();
        // An empty note is fine when a screenshot is attached — the image is the note.
        if (!body && !(composeMode !== 'edit' && composeMedia)) { closeComposer(); editor.commands.focus(); return; }
        if (composeMode === 'edit') {
            closeComposer();
            opts.onCommentEdit(composeThreadId, body);
            markSaving('commented');
            editor.commands.focus();
            return;
        }
        if (!composeRange) { closeComposer(); return; }
        const { from, to } = composeRange;
        const markType = editor.state.schema.marks.comment;
        if (!markType) { closeComposer(); return; }
        commentMutate(tr => tr.addMark(from, to, markType.create({ threadId: composeThreadId })));
        markSaving('commented');
        closeComposer();
        // Collapse to the span end so the bubble doesn't immediately re-appear over
        // the still-selected text.
        editor.commands.setTextSelection(to);
        const thread: CommentThread = {
            id: composeThreadId,
            featureId: composeFid,
            anchorText: composeAnchor,
            body: body || '(see screenshot)',
            status: 'open',
            author: 'human',
            createdAt: Date.now(),
        };
        const media = composeMedia ? { data: composeMedia.data, mime: composeMedia.mime } : undefined;
        composeMedia = null;
        opts.onCommentCreate(editor.getJSON() as PMNode, thread, media);
        editor.commands.focus();
    }

    function resolveComment(id: string): void {
        const range = commentMarkRange(id);
        const markType = editor.state.schema.marks.comment;
        if (range && markType) commentMutate(tr => tr.removeMark(range.from, range.to, markType));
        markSaving('');
        opts.onCommentResolve(editor.getJSON() as PMNode, id);
    }

    // ── comment margin (Notion-style cards in the right whitespace) ───────────────
    // Threads render as persistent cards in the doc's right margin, vertically aligned to
    // their anchored text and de-overlapped top-to-bottom. They live INSIDE the scrolling
    // surface (absolute, content-space `top`) so they scroll with the prose for free — only
    // a doc/width change moves an anchor, so we only re-lay-out then (not on scroll). The
    // surface gains `.has-comments` (CSS shifts the prose left to make room) whenever a
    // thread or the composer is present — otherwise the margin is empty whitespace ("collapsed").
    const CMT_STACK_GAP = 10;
    function elc(tag: string, cls?: string, text?: string): HTMLElement {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }
    function clearMarginCards(): void {
        surface.querySelectorAll('.ce-cmt-card').forEach(n => n.remove());
    }
    /** A doc position's top in the surface's SCROLL (content) space — stable across scroll. */
    function anchorTopInContent(pos: number): number | null {
        try {
            const c = editor.view.coordsAtPos(pos);
            return c.top - surface.getBoundingClientRect().top + surface.scrollTop;
        } catch { return null; }
    }
    function syncCommentsPresence(): void {
        surface.classList.toggle('has-comments', currentComments.length > 0 || composer !== null);
    }
    function relTime(ts: number): string {
        const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
        if (s < 45) return 'just now';
        const m = Math.round(s / 60);
        if (m < 60) return `${m}m`;
        const h = Math.round(m / 60);
        return h < 24 ? `${h}h` : `${Math.round(h / 24)}d`;
    }
    function cardAction(label: string, onClick: () => void, cls = ''): HTMLButtonElement {
        const b = document.createElement('button');
        b.type = 'button'; b.className = ('ce-cmt-action ' + cls).trim(); b.textContent = label;
        b.addEventListener('mousedown', ev => ev.preventDefault());
        b.addEventListener('click', ev => { ev.preventDefault(); ev.stopPropagation(); onClick(); });
        return b;
    }
    function buildMarginCard(t: CommentThread): HTMLElement {
        const card = document.createElement('div');
        card.className = 'ce-cmt-card ' + (t.status === 'sent' ? 'sent' : 'open');
        card.contentEditable = 'false';
        const head = document.createElement('div');
        head.className = 'ce-cmt-card-head';
        head.append(elc('span', 'ce-cmt-who', t.author === 'human' ? 'You' : t.author));
        head.append(elc('span', 'ce-cmt-time', relTime(t.createdAt)));
        head.append(elc('span', 'ce-cmt-state', t.status === 'sent' ? '✓ sent' : '→ for agent'));
        card.append(head);
        if (t.anchorText.trim()) {
            const a = elc('div', 'ce-cmt-anchor'); a.textContent = t.anchorText; a.title = t.anchorText;
            card.append(a);
        }
        card.append(elc('div', 'ce-cmt-body', t.body));
        const foot = elc('div', 'ce-cmt-foot');
        if (t.status !== 'sent') foot.append(cardAction('Edit', () => openComposerForEdit(t)));
        foot.append(cardAction('Resolve', () => resolveComment(t.id), 'ce-cmt-resolve'));
        card.append(foot);
        // click the card body → reveal + select its anchored text
        card.addEventListener('mousedown', ev => {
            if ((ev.target as HTMLElement).closest('button')) return;
            ev.preventDefault();
            const r = commentMarkRange(t.id);
            if (r) editor.chain().focus().setTextSelection({ from: r.from, to: r.to }).run();
        });
        return card;
    }
    /** Re-lay-out the margin cards: build, sort by anchor top, push down to avoid overlap. */
    function renderCommentMargin(): void {
        clearMarginCards();
        syncCommentsPresence();
        if (!currentComments.length) return;
        const items: { card: HTMLElement; top: number }[] = [];
        for (const t of currentComments) {
            const range = commentMarkRange(t.id);
            if (!range) continue;                       // mark gone (resolved) → no card
            const top = anchorTopInContent(range.from);
            if (top == null) continue;
            items.push({ card: buildMarginCard(t), top });
        }
        items.sort((a, b) => a.top - b.top);
        let cursor = -Infinity;
        for (const it of items) {
            surface.appendChild(it.card);               // append first so offsetHeight is real
            const top = Math.max(it.top, cursor);
            it.card.style.top = `${Math.round(top)}px`;
            cursor = top + it.card.offsetHeight + CMT_STACK_GAP;
        }
    }
    function positionComposerInMargin(): void {
        if (!composer || !composeRange) return;
        const top = anchorTopInContent(composeRange.from);
        if (top != null) composer.style.top = `${Math.round(top)}px`;
    }
    // Coalesce reflows during a typing burst (one re-lay-out per frame, not per keystroke).
    let cmtReflowQueued = false;
    function scheduleCommentReflow(): void {
        if (cmtReflowQueued) return;
        cmtReflowQueued = true;
        requestAnimationFrame(() => { cmtReflowQueued = false; positionComposerInMargin(); renderCommentMargin(); });
    }

    // Reposition / dismiss the bubble + composer as the surface scrolls or blurs.
    let blurTimer = 0;
    surface.addEventListener('scroll', () => { if (bubble.style.display !== 'none') updateBubble(); }, { passive: true });
    editor.view.dom.addEventListener('blur', () => {
        if (blurTimer) clearTimeout(blurTimer);
        blurTimer = window.setTimeout(() => {
            blurTimer = 0;
            if (document.activeElement && bubble.contains(document.activeElement)) return;
            closeBubble();
        }, 100);
    });

    // Window resize (U5): re-anchor the interactive floating surfaces (selection bubble +
    // comment composer) against the new viewport so they don't sit at stale coordinates; the
    // composer recomputes from its captured range live and dismisses if that text scrolled off.
    // The transient hover popovers (threads peek, comment/hover card) close themselves on resize
    // from their own modules.
    function repositionFloatingSurfaces(): void {
        if (bubble.style.display !== 'none') updateBubble();
        // a width change reflows the prose → margin anchors moved; re-place the composer + cards.
        positionComposerInMargin();
        renderCommentMargin();
    }
    window.addEventListener('resize', repositionFloatingSurfaces);

    // Text typed since the last settle exists ONLY in this editor's memory: the
    // debounce is trailing-edge, so a burst with no 1.2 s gap in it has never been
    // sent anywhere. Every way out of this editor therefore flushes first — the
    // panel closing, the window hiding, the tab going away. `settleNow` is a no-op
    // when nothing is dirty, so this costs nothing in the common case.
    const flushOnHide = (): void => { if (document.visibilityState === 'hidden') settleNow(true); };
    const settleOnPagehide = (): void => settleNow(true);
    window.addEventListener('pagehide', settleOnPagehide);
    document.addEventListener('visibilitychange', flushOnHide);

    const handle: WholeDocEditorHandle = {
        element: wrap,
        setDoc: (incoming: PMNode, baselineId?: number) => {
            patchMintedIds(incoming); // learn minted ids even mid-edit (prevents a double-add)
            // A reload while a comment composer / bubble is open would remap or destroy
            // the captured range under it (the "selection vanished mid-comment" bug).
            // DEFER the WHOLE update (keeping only the latest) and re-apply it the
            // moment the composer/bubble closes — so the doc never sits stale waiting
            // on an unrelated next write (W5 composer-drop fix).
            // Deferral now also covers active typing (activelyEditing): adopting
            // mid-thought both yanked the caret (absolute-position restore into a
            // reshaped doc) and force-settled the half-typed fragment below — which
            // round-tripped into ANOTHER projection, the feedback loop that shipped
            // a title as "D" → "Dra" → "Draf". The parked projection re-applies via
            // the retry flush once typing stops, or on blur/commit.
            if (isComposing()) {
                pendingProjection = { doc: incoming, baselineId };
                scheduleProjectionFlush();
                return;
            }
            // Flush before anything can replace the document. The version gate below
            // resolves a same-feature disagreement by swapping a whole slice, and when
            // the projection wins, whatever the user had typed since the last settle
            // goes with it — unsent, unrecorded, gone. Settling first puts that text on
            // the wire, where the daemon's three-way merge decides its fate honestly:
            // it applies, merges with whoever else wrote, or is kept for review. Every
            // path ends with it existing somewhere — which is the point, because the
            // gate's own resolution is a slice swap that ends with it existing nowhere.
            // `settleNow` is a no-op unless the user actually typed. Forced: reaching
            // here means the author is NOT mid-thought (the defer above), so a caret
            // parked in a heading must not hold the settle hostage.
            settleNow(true);
            // U5 per-feature HLC version gate (R14 / KTD4) — replaces the whole-doc
            // `if (dirty) return`. Merge the incoming projection with the live doc PER
            // FEATURE: a feature with no pending local edit adopts the projection; a
            // feature with a pending edit keeps its optimistic local copy UNLESS the
            // projected per-feature version is strictly newer. An advance on an
            // unrelated feature therefore never reverts a pending edit on another.
            const local = editor.getJSON() as PMNode;
            const gate = gateProjection({ incoming, local, localVersions, pendingFids });
            const doc = gate.doc;
            // Fold the adopted per-feature versions into the local tracking and clear
            // their pending edits — those features are now in sync with the projection.
            // `projected` is also what a later edit is compared against to decide the
            // feature is back in sync, so it is recorded here, before the echo
            // short-circuit below can return.
            const projected = featureBlocks(doc);
            for (const [fid, v] of gate.adopted) {
                localVersions.set(fid, v);
                pendingFids.delete(fid);
                const ft = projected.get(fid);
                if (ft) adoptedText.set(fid, ftKey(ft));
            }
            // The citation advances HERE — after the flush above sent the previous
            // baseline's content, and before either exit path below. Every later settle
            // therefore diffs against this projection, which is the one the author is
            // about to be looking at.
            //
            // It advances even when the gate kept a feature local: this projection is
            // still what the author saw, and their unsent edit to that feature diffs
            // against it as the edit it is. Its `base_text` comes from the host's
            // optimistic overlay (the text it already emitted), not from here.
            adoptedBaselineId = baselineId;
            // Skip the reload when BOTH the baseline text AND the agent-proposal set
            // are unchanged — the common case right after a settle round-trips;
            // reloading would reset the caret. Reload when the baseline text changed
            // OR an agent proposal appeared/resolved (its marks live in `doc` but
            // render to the same baseline, so a text-only compare would miss them).
            const sig = proposalsSig();
            const sameText = renderTreeFromDoc(doc) === renderTreeFromDoc(local);
            if (sameText && sig === lastProposalsSig) {
                markSaving('');
                // The daemon just echoed back what the user already has — do NOT re-baseline.
                // The captured edit persists (visible) until the user stages & sends (commit).
                return;
            }
            // A real reload (external/agent change, or first load) → re-baseline the
            // captured set, but only for the features that actually ADOPTED this
            // projection. A feature the gate kept local keeps its own baseline, so an
            // unrelated daemon write can no longer erase the change marks under the
            // user's cursor. Commit is the other re-baseline point (commitNow).
            capturedBaseline = rebaseCaptured(capturedBaseline, projected, new Set(gate.adopted.keys()));
            lastProposalsSig = sig;

            const keepFid = activeFid();          // stable anchor for existing features
            const keepIndex = activeHeadingIndex(); // fallback for a brand-new (fid:null) heading
            const savedPos = editor.state.selection.from; // the user's actual caret — keep it
            suppressUpdate = true;
            try {
                // Replace the whole doc with a REFLECT-tagged transaction so the
                // authorship-stamp plugin does NOT treat this programmatic load as
                // user input (otherwise every reload re-stamps the entire doc).
                const node = editor.state.schema.nodeFromJSON(doc);
                if (node.content.size > 0) {
                    const tr = editor.state.tr
                        .replaceWith(0, editor.state.doc.content.size, node.content)
                        .setMeta(REFLECT_META, true)
                        .setMeta('addToHistory', false);
                    editor.view.dispatch(tr);
                }
            } catch {
                editor.commands.setContent(doc as unknown as Record<string, unknown>, false);
            } finally {
                // MUST be `finally`: if anything below throws while this is still set,
                // `onUpdate` returns early forever and the editor silently stops
                // settling — the user keeps typing into a surface that no longer saves.
                suppressUpdate = false;
            }
            // Keep the user's caret WHERE IT WAS instead of yanking it to the feature
            // heading. The common case is a settle round-trip where the new doc is ~identical,
            // so restoring the pre-reload absolute position (clamped) keeps the caret exactly
            // put; a larger change just lands nearby. Only when there was no real caret
            // (position at doc start) do we fall back to the active heading.
            // `lastSelection` (the toolbar/bubble anchor) is still dropped — a stale range
            // there could stamp the wrong span.
            lastSelection = null;
            const maxPos = Math.max(1, editor.state.doc.content.size - 1);
            let restorePos: number | null = savedPos > 0 ? Math.min(savedPos, maxPos) : null;
            if (restorePos == null) {
                const head = (keepFid ? headingPosForFid(editor, keepFid) : null) ?? headingPosAtIndex(keepIndex);
                restorePos = head == null ? null : head + 1;
            }
            if (restorePos != null) {
                editor.view.dispatch(editor.state.tr.setSelection(TextSelection.near(editor.state.doc.resolve(restorePos))));
            }
            markSaving('');
            rebuildRail();
            requestAnimationFrame(renderCommentMargin); // anchors moved → re-place margin cards
        },
        setSuggestions: (list: Suggestion[]) => {
            currentSuggestions = list;  // agent code-ahead proposals (the host's sidecar)
            editor.view.dispatch(editor.state.tr.setMeta(SUGGESTIONS_UPDATED, true));
            scheduleRail();   // the minimap shows `proposed`
        },
        setThreads: (threadsMap: Record<string, ThreadsData>) => {
            currentThreads = threadsMap;
            editor.view.dispatch(editor.state.tr.setMeta(DEPS_UPDATED, true));
        },
        setComments: (comments: CommentThread[]) => {
            currentComments = comments;
            requestAnimationFrame(renderCommentMargin); // persistent margin cards follow the store
        },
        setHoverCards: (cards: HoverCardData | null) => {
            // Pure overlay data — no doc transaction; the handler reads it lazily on
            // the next hover, so a re-render mid-hover picks up fresh cards.
            currentHoverCards = cards;
        },
        setPhases: (phases: Record<string, FeaturePhase>) => {
            currentPhases = phases;
            // One transaction carries both metas: the heading dot (PHASES_UPDATED) and the
            // body ghost→reveal (REVEAL_UPDATED) read the same phase map.
            editor.view.dispatch(editor.state.tr.setMeta(PHASES_UPDATED, true).setMeta(REVEAL_UPDATED, true));
            scheduleRail();   // the minimap shows `working`
        },
        setSteps: (steps: Record<string, AgentStep[]>) => {
            currentSteps = steps;
            editor.view.dispatch(editor.state.tr.setMeta(STEPS_UPDATED, true));
        },
        setAutoEdits: (edits: Record<string, AutoEditInfo>) => {
            currentAutoEdits = edits;
            editor.view.dispatch(editor.state.tr.setMeta(AUTO_EDITS_UPDATED, true));
            scheduleRail();   // the minimap shows `rewritten`
        },
        setBusy: (busy: Record<string, BusyInfo>) => {
            currentBusy = new Map(Object.entries(busy));
            editor.view.dispatch(editor.state.tr.setMeta(BUSY_UPDATED, true));
            scheduleRail();   // the minimap shows `busy`
        },
        setRole: (role: string) => {
            currentRole = role;
            editor.view.dispatch(editor.state.tr.setMeta(STEPS_UPDATED, true));
        },
        setHeld: (fids: string[], detail?: Record<string, HoldDetail>) => {
            currentHeld = new Set(fids);   // the HANDED-OFF set (staged & sent) → pending badge
            currentHoldDetail = detail ?? {};
            // Held changes also move the captured set (handed-off suppresses captured), so
            // recompute both families in one transaction.
            editor.view.dispatch(editor.state.tr.setMeta(HOLDS_UPDATED, true)
                .setMeta(CAPTURED_UPDATED, true).setMeta(SUGGESTIONS_UPDATED, true));
            scheduleRail();   // the minimap shows `sent`
        },
        setSessionLive: (live: boolean) => {
            if (currentSessionLive === live) return;
            currentSessionLive = live;
            editor.view.dispatch(editor.state.tr.setMeta(HOLDS_UPDATED, true)); // re-word tooltips
        },
        setHistory: (history: Record<string, HistoryEntry[]>) => {
            currentHistory = history;
            editor.view.dispatch(editor.state.tr.setMeta(BLAME_UPDATED, true));
        },
        setBlame: (on: boolean) => {
            if (currentBlame === on) return;
            currentBlame = on;
            editor.view.dispatch(editor.state.tr.setMeta(BLAME_UPDATED, true));
        },
        setDrafts: (fids: string[]) => {
            currentDrafts = new Set(fids);  // recorded, not yet handed off → captured
            // Also re-run the suggestion layer: the draft set feeds its "you edited this"
            // contest note, so a new draft on a proposed feature must repaint the strip.
            editor.view.dispatch(editor.state.tr
                .setMeta(CAPTURED_UPDATED, true).setMeta(SUGGESTIONS_UPDATED, true));
            scheduleRail();   // the minimap shows `staged`
        },
        setBlocks: (blocks: Record<string, UIBlock[]>) => {
            currentBlocks = blocks;
            editor.view.dispatch(editor.state.tr.setMeta(BLOCKS_UPDATED, true));
        },
        setMintedMap: (m: Record<string, string>) => { currentMintedByLocalId = m; },
        setPitches: (pitches: Record<string, string>) => {
            currentPitches = pitches;
            // Refresh glance widgets if glance is currently on (no-op decoration when off).
            editor.view.dispatch(editor.state.tr.setMeta(GLANCE_UPDATED, true));
        },
        setGlance: (on: boolean) => {
            if (glanceOn === on) return;
            glanceOn = on;
            // Body class drives the CSS that hides descriptions; the plugin draws the pitch.
            document.body.classList.toggle('glance', on);
            editor.view.dispatch(editor.state.tr.setMeta(GLANCE_UPDATED, true));
        },
        scrollToFeature: (fid: string) => scrollToFeatureInternal(fid, false),
        commit: () => commitNow(),
        createFeature: (title: string) => {
            // P4 / §D.3: mint a new top-level feature from the ⌘K "Create feature" affordance —
            // append a level-0 featureHeading (fid:null; the host mints the id on settle) at the
            // end of the doc with the title, move the caret into it, and let the normal settle
            // flow persist + mint it (the same path the `#`-input-rule heading uses).
            const t = title.trim();
            if (!t) return;
            const heading = editor.schema.nodes.featureHeading.create(
                // A localId, like every other creation path. Without one this node has
                // NO identity until the daemon mints its fid: it is dropped from the
                // captured set (so it shows no "recorded" mark), and the mint that
                // eventually arrives cannot be matched to it by id, falling back to
                // guessing by title and document order — which binds the wrong fid
                // outright when two features are created in quick succession.
                { fid: null, localId: newLocalId(), level: 0, retired: false, realized: true },
                editor.schema.text(t),
            );
            const end = editor.state.doc.content.size;
            const tr = editor.state.tr.insert(end, heading);
            tr.setSelection(TextSelection.near(tr.doc.resolve(end + t.length + 1)));
            editor.view.dispatch(tr.scrollIntoView());
            editor.commands.focus();
            commitNow();   // persist + hand to the host so it mints the fid
        },
        getCaretPos: () => editor.state.selection.from,
        setCaretPos: (pos: number) => {
            const max = Math.max(1, editor.state.doc.content.size - 1);
            const p = Math.max(0, Math.min(pos, max));
            editor.view.dispatch(editor.state.tr.setSelection(TextSelection.near(editor.state.doc.resolve(p))));
        },
        getScrollTop: () => surface.scrollTop,
        setScrollTop: (n: number) => { surface.scrollTop = Math.max(0, n); },
        isDirty: () => dirty,
        touchFeatures: (fids: string[], big?: Set<string>) => touchFeaturesInternal(fids, big),
        destroy: () => {
            // Never discard an unsent edit on the way out — but never let a failed
            // send abort teardown either, or the listeners and timers below outlive
            // the editor they belong to.
            try { settleNow(true); } catch { /* best effort: teardown must still finish */ }
            if (settleTimer) clearTimeout(settleTimer);
            if (railTimer) clearTimeout(railTimer);
            if (muteTimer) clearTimeout(muteTimer);
            if (blurTimer) clearTimeout(blurTimer);
            if (navTween) navTween.cancel();          // stop an in-flight momentum scroll
            for (const t of touchTimers.values()) clearTimeout(t); // P2 code→doc spark timers
            for (const t of tickTimers.values()) clearTimeout(t);   // P2 fix 2 tick-lifetime timers
            window.removeEventListener('resize', repositionFloatingSurfaces);
            window.removeEventListener('pagehide', settleOnPagehide);
            document.removeEventListener('visibilitychange', flushOnHide);
            if (spyRaf) cancelAnimationFrame(spyRaf); // else the RAF fires onActiveFeature on a destroyed editor
            closeComposer();
            bubble.remove();
            detachHoverCards();        // tear down the hover-card listeners + open card
            resetCommentDecorations(); // tear down the module-level popover + hover timer
            editor.destroy();
        },
    };
    return handle;
}
