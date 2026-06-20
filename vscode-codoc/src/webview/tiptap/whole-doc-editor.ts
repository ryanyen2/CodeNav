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
import { CodeRefSuggestion, RefSymbol } from './code-ref-suggestion';
import {
    indentHeading,
    outdentHeading,
    newFeatureHeading,
    toggleRetireHeading,
    headingPosForFid,
} from './structure-commands';
import { SuggestionDecorations, SUGGESTIONS_UPDATED, DependencyDecorations, DEPS_UPDATED } from './suggestion-decorations';
import { ActivityDecorations, PHASES_UPDATED } from './activity-decorations';
import { HoldDecorations, HOLDS_UPDATED } from './hold-decorations';
import { CapturedDecorations, CAPTURED_UPDATED, featureBlocks, type FeatureText } from './captured-decorations';
import { GlanceDecorations, GLANCE_UPDATED } from './glance-decorations';
import { CommentDecorations, COMMENTS_UPDATED, resetCommentDecorations } from './comment-decorations';
import { attachHoverCards, HoverCardData } from './hover-card';
import { renderTreeFromDoc } from '../../state/doc-serialize';
import { mintCommentId, CommentThread } from '../../state/comment-model';
import type { HoldDetail } from '../../state/bindings-model';
import type { Suggestion } from '../../state/suggestion-model';
import { inlineRunsToText, type PMNode } from '../../state/pm-doc';
import { tweenScrollTop, navDuration, muteWindowFor, prefersReducedMotion, staggerHover, sparkIn, type TweenController } from '../motion';
import { icon } from '../icons';
import type { FeaturePhase } from '../../state/activity-model';
import type { ThreadsData } from '../protocol';

export interface WholeDocEditorOptions {
    controller: AuthorController;
    getSymbols: () => RefSymbol[];
    /** Commit the whole settled doc (debounced). The single edit path — captures locally. */
    onSettle: (doc: PMNode) => void;
    /** Stage & SEND (U4): the explicit Save/Commit gesture (⌘S or the Commit button).
     *  Flushes the latest edit and hands the staged code-implying edits to the agent. */
    onCommit?: (doc: PMNode) => void;
    onAccept: (s: Suggestion) => void;
    onReject: (s: Suggestion) => void;
    /** Withdraw a queued realization for a feature (U6) — the ✕ on its "realizing"
     *  badge. Cancels the directive, keeps the prose. */
    onWithdrawRealization: (featureId: string) => void;
    onOpenBinding: (file: string, symbol: string) => void;
    /** Open a Consult strand link (a description's external `https://` page). */
    onConsult: (url: string) => void;
    /** Create an inline comment: the whole doc (carrying the new anchor mark) + the thread. */
    onCommentCreate: (doc: PMNode, thread: CommentThread) => void;
    /** Edit a comment's body in place. */
    onCommentEdit: (id: string, body: string) => void;
    /** Resolve a comment: the whole doc (anchor mark removed) + the thread id. */
    onCommentResolve: (doc: PMNode, id: string) => void;
    /** The active feature changed — drives the tree-pane highlight, and (only when
     *  `source==='scroll'`) the eased tree re-center. `'selection'` fires on every caret move,
     *  so re-centering on it would animate the tree on every keystroke (KTD2). */
    onActiveFeature?: (fid: string | null, source: 'scroll' | 'selection') => void;
    /** Pointer hovering a depends-on / used-by link — drives a transient tree-pane
     *  highlight + scroll-to (preview navigation). null on leave. */
    onHoverFeature?: (fid: string | null) => void;
    /** The user edited the feature the caret is in (P2 / §A.1 doc→code bridge). Fires on
     *  every keystroke with the owning fid (null when not in a feature); the webview
     *  debounces it 180 ms and opens that feature's bound code Beside. */
    onEditFeature?: (fid: string | null) => void;
}

export interface WholeDocEditorHandle {
    element: HTMLElement;
    /** Re-seed from an external payload (skipped while the user has unsettled edits). */
    setDoc: (doc: PMNode) => void;
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
    /** Update the "awaiting AI realization" set (the daemon hold set) — drives the
     *  pending-intent rail + underline + being-realized badge. `detail` carries the
     *  queued directive's kind + intent gloss per feature (a subset of `fids`) for the
     *  rail's hover title; omit it (tests) for the plain rail. */
    setHeld: (fids: string[], detail?: Record<string, HoldDetail>) => void;
    /** Held drafts (U3): edits recorded & staged locally but NOT yet handed off — drives
     *  the "captured" mark (alongside the client-side changed-vs-baseline set). */
    setDrafts: (fids: string[]) => void;
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
        addKeyboardShortcuts() {
            const ed = this.editor;
            return {
                Tab: () => indentHeading(ed),
                'Shift-Tab': () => outdentHeading(ed),
                // ⌘S / Ctrl-S = "save the file" → stage & send (U4). The host never dirties
                // the text document (single-writer), so the native save is a no-op we
                // repurpose; returning true preventDefaults it so no save dialog flashes.
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
    let suppressUpdate = false;          // true while we programmatically setContent
    let currentSuggestions: Suggestion[] = [];   // agent code-ahead proposals (sidecar)
    // Signature of the agent code-ahead AMENDs last LOADED into the doc as engine
    // marks. setDoc reloads when this changes even if the baseline text is identical
    // — so a newly-arrived agent proposal's marks appear, and a rejected one's marks
    // clear (reject leaves the baseline unchanged, so only the signature moves).
    let lastProposalsSig = '';
    let currentThreads: Record<string, ThreadsData> = {};
    let currentPhases: Record<string, FeaturePhase> = {};
    let currentHeld = new Set<string>();   // handed-off features (staged & sent) → pending badge
    let currentHoldDetail: Record<string, HoldDetail> = {};  // queued-directive {kind,intent} per held fid
    // Edit-lifecycle phase 1 (U3): the "captured" set is computed in the plugin from
    // (live doc vs baseline) ∪ drafts, minus handed-off. The baseline is the feature text
    // as of the LAST COMMIT (or last external reload) — frozen across the daemon's
    // self-echo round-trip so a captured edit (add OR delete) persists until ⌘S/Commit.
    let capturedBaseline = new Map<string, FeatureText>();
    let currentDrafts = new Set<string>();
    let currentComments: CommentThread[] = [];
    let currentHoverCards: HoverCardData | null = null;
    let currentPitches: Record<string, string> = {}; // B-U2 glance: fid → pitch
    let glanceOn = false;
    // Last NON-EMPTY selection — a fallback so a bubble action still has a range to act
    // on if focus moved and the live selection collapsed (the "comment did nothing" bug).
    let lastSelection: { from: number; to: number } | null = null;

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
            }),
            DependencyDecorations.configure({
                getThreads: () => currentThreads,
                onNavigate: fid => scrollToFeatureInternal(fid, true),
                onOpenBinding: opts.onOpenBinding,
                onConsult: opts.onConsult,
            }),
            ActivityDecorations.configure({ getPhases: () => currentPhases }),
            HoldDecorations.configure({
                getHeld: () => currentHeld,
                getDetail: () => currentHoldDetail,
                onWithdraw: opts.onWithdrawRealization,
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
            CommentDecorations.configure({
                getComments: () => currentComments,
                handlers: {
                    resolve: (id: string) => resolveComment(id),
                    edit: (thread: CommentThread) => openComposerForEdit(thread),
                },
            }),
            makeKeymap(() => commitNow()),
        ],
        content: { type: 'doc', content: [{ type: 'paragraph' }] },
        autofocus: false,
        onUpdate: () => {
            if (suppressUpdate) return;
            dirty = true;
            scheduleSettle();
            scheduleRail();
            // P2 doc→code bridge: the live edit's owning feature — the webview debounces
            // this and opens that feature's bound code Beside (§A.1).
            const editedFid = activeFid();
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

    function settleNow(): void {
        if (settleTimer) { clearTimeout(settleTimer); settleTimer = 0; }
        if (!dirty) return;
        dirty = false;
        // ONE edit path (U3): the human's edit always COMMITS. The daemon classifies
        // it (pure-doc vs code-implying) and, when it implies code, lands the feature
        // in the hold set → the calm "being realized" badge surfaces back. No
        // client-side suggest/strip/dual-state.
        opts.onSettle(editor.getJSON() as PMNode);
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
        opts.onCommit?.(doc);
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
            tick.className = 'ce-tick';
            tick.style.setProperty('--d', String(Math.min(Number(node.attrs.level) || 0, 4)));
            if (node.attrs.retired) tick.classList.add('retired');
            if (node.attrs.realized === false) tick.classList.add('unrealized');
            tick.title = node.textContent || '(untitled)';
            tick.addEventListener('click', () => scrollToFeatureInternal(fid, true));
            tickByFid.set(fid, tick);
            tickList.push(tick);
            rail.append(tick);
        });
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
            const threshold = surface.getBoundingClientRect().top + 72;
            let current: string | null = null;
            editor.state.doc.forEach((node, pos) => {
                if (node.type.name !== 'featureHeading') return;
                const fid = node.attrs.fid as string | null;
                if (!fid) return;
                const dom = headingDom(pos);
                if (dom && dom.getBoundingClientRect().top <= threshold) current = fid;
            });
            markCurrent(current);
            // Only notify on an actual section change — scroll-spy runs every RAF frame,
            // and onActiveFeature drives the tree highlight + dependency spotlight recompute
            // (an O(rows) DOM pass), so firing it per-frame thrashes a large tree.
            if (current && current !== lastSpyFid) { lastSpyFid = current; opts.onActiveFeature?.(current, 'scroll'); }
        });
    }
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
        const minted: { title: string; fid: string }[] = [];
        for (const b of incoming.content ?? []) {
            if (b.type !== 'featureHeading') continue;
            const fid = (b.attrs as { fid?: string | null } | undefined)?.fid;
            if (fid && !localFids.has(fid)) minted.push({ title: inlineRunsToText(b.content).trim(), fid });
        }
        if (!minted.length) return;

        const used = new Set<number>();
        let tr = editor.state.tr;
        let changed = false;
        editor.state.doc.forEach((node, pos) => {
            if (node.type.name !== 'featureHeading' || node.attrs.fid != null) return;
            const title = (node.textContent || '').trim();
            let pick = minted.findIndex((m, i) => !used.has(i) && m.title === title);
            if (pick < 0) pick = minted.findIndex((_m, i) => !used.has(i)); // order fallback
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
    let composeFid: string | null = null;
    let composeAnchor = '';
    let composeThreadId = '';


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
    function closeBubble(): void { bubble.style.display = 'none'; }

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
        // Anchor the composer to the captured range (the live rect may be gone if the
        // selection collapsed) so it always opens beside the text it comments on.
        openComposer(coordsRect(from, to), '');
    }

    function openComposerForEdit(thread: CommentThread): void {
        composeMode = 'edit';
        composeThreadId = thread.id;
        composeRange = commentMarkRange(thread.id);
        composeFid = thread.featureId;
        composeAnchor = thread.anchorText;
        const at = composeRange ? coordsRect(composeRange.from, composeRange.to) : null;
        openComposer(at, thread.body);
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

    function openComposer(at: DOMRect | null, initial: string): void {
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
        const send = document.createElement('button');
        send.type = 'button';
        send.className = 'ce-cmt-send';
        send.textContent = composeMode === 'edit' ? 'Save' : 'Send';
        send.addEventListener('mousedown', ev => ev.preventDefault());
        send.addEventListener('click', ev => { ev.preventDefault(); saveComposer(ta.value); });
        foot.append(hint, send);
        box.append(ta, foot);
        document.body.append(box);
        composer = box;
        const w = box.offsetWidth || 240;
        const left = at ? Math.max(8, Math.min(at.left, window.innerWidth - w - 8)) : (window.innerWidth - w) / 2;
        const top = at ? Math.min(at.bottom + 6, window.innerHeight - box.offsetHeight - 8) : 120;
        box.style.left = `${left}px`;
        box.style.top = `${top}px`;
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
        if (!body) { closeComposer(); editor.commands.focus(); return; }
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
            body,
            status: 'open',
            author: 'human',
            createdAt: Date.now(),
        };
        opts.onCommentCreate(editor.getJSON() as PMNode, thread);
        editor.commands.focus();
    }

    function resolveComment(id: string): void {
        const range = commentMarkRange(id);
        const markType = editor.state.schema.marks.comment;
        if (range && markType) commentMutate(tr => tr.removeMark(range.from, range.to, markType));
        markSaving('');
        opts.onCommentResolve(editor.getJSON() as PMNode, id);
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
        if (composer && composeRange) {
            const at = coordsRect(composeRange.from, composeRange.to);
            if (!at) { closeComposer(); return; }
            const w = composer.offsetWidth || 240;
            composer.style.left = `${Math.max(8, Math.min(at.left, window.innerWidth - w - 8))}px`;
            composer.style.top = `${Math.min(at.bottom + 6, window.innerHeight - composer.offsetHeight - 8)}px`;
        }
    }
    window.addEventListener('resize', repositionFloatingSurfaces);

    return {
        element: wrap,
        setDoc: (doc: PMNode) => {
            patchMintedIds(doc); // learn minted ids even mid-edit (prevents a double-add)
            if (dirty) return;   // otherwise don't clobber unsettled edits
            // A reload while a comment composer / bubble is open would remap or destroy
            // the captured range under it (the "selection vanished mid-comment" bug).
            // Defer the WHOLE update — the next payload reloads cleanly.
            if (composer || bubble.style.display !== 'none') return;
            // Skip the reload when BOTH the baseline text AND the agent-proposal set
            // are unchanged — the common case right after a settle round-trips;
            // reloading would reset the caret. Reload when the baseline text changed
            // OR an agent proposal appeared/resolved (its marks live in `doc` but
            // render to the same baseline, so a text-only compare would miss them).
            const sig = proposalsSig();
            const sameText = renderTreeFromDoc(doc) === renderTreeFromDoc(editor.getJSON() as PMNode);
            if (sameText && sig === lastProposalsSig) {
                markSaving('');
                // The daemon just echoed back what the user already has — do NOT re-baseline.
                // The captured edit persists (visible) until the user stages & sends (commit).
                return;
            }
            // A real reload (external/agent change, or first load) → re-baseline the captured
            // set against the new canonical doc. Commit is the other re-baseline point
            // (commitNow), so a user's own uncommitted edits don't clear here.
            capturedBaseline = featureBlocks(doc);
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
            }
            suppressUpdate = false;
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
        },
        setSuggestions: (list: Suggestion[]) => {
            currentSuggestions = list;  // agent code-ahead proposals (the host's sidecar)
            editor.view.dispatch(editor.state.tr.setMeta(SUGGESTIONS_UPDATED, true));
        },
        setThreads: (threadsMap: Record<string, ThreadsData>) => {
            currentThreads = threadsMap;
            editor.view.dispatch(editor.state.tr.setMeta(DEPS_UPDATED, true));
        },
        setComments: (comments: CommentThread[]) => {
            currentComments = comments;
            editor.view.dispatch(editor.state.tr.setMeta(COMMENTS_UPDATED, true));
        },
        setHoverCards: (cards: HoverCardData | null) => {
            // Pure overlay data — no doc transaction; the handler reads it lazily on
            // the next hover, so a re-render mid-hover picks up fresh cards.
            currentHoverCards = cards;
        },
        setPhases: (phases: Record<string, FeaturePhase>) => {
            currentPhases = phases;
            editor.view.dispatch(editor.state.tr.setMeta(PHASES_UPDATED, true));
        },
        setHeld: (fids: string[], detail?: Record<string, HoldDetail>) => {
            currentHeld = new Set(fids);   // the HANDED-OFF set (staged & sent) → pending badge
            currentHoldDetail = detail ?? {};
            // Held changes also move the captured set (handed-off suppresses captured), so
            // recompute both families in one transaction.
            editor.view.dispatch(editor.state.tr.setMeta(HOLDS_UPDATED, true).setMeta(CAPTURED_UPDATED, true));
        },
        setDrafts: (fids: string[]) => {
            currentDrafts = new Set(fids);  // recorded, not yet handed off → captured
            editor.view.dispatch(editor.state.tr.setMeta(CAPTURED_UPDATED, true));
        },
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
                { fid: null, level: 0, retired: false, realized: true },
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
            if (settleTimer) clearTimeout(settleTimer);
            if (railTimer) clearTimeout(railTimer);
            if (muteTimer) clearTimeout(muteTimer);
            if (blurTimer) clearTimeout(blurTimer);
            if (navTween) navTween.cancel();          // stop an in-flight momentum scroll
            for (const t of touchTimers.values()) clearTimeout(t); // P2 code→doc spark timers
            for (const t of tickTimers.values()) clearTimeout(t);   // P2 fix 2 tick-lifetime timers
            window.removeEventListener('resize', repositionFloatingSurfaces);
            if (spyRaf) cancelAnimationFrame(spyRaf); // else the RAF fires onActiveFeature on a destroyed editor
            closeComposer();
            bubble.remove();
            detachHoverCards();        // tear down the hover-card listeners + open card
            resetCommentDecorations(); // tear down the module-level popover + hover timer
            editor.destroy();
        },
    };
}
