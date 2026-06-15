/**
 * whole-doc-editor.ts — ONE TipTap editor over the entire feature tree (R3, slice 1).
 *
 * Headings are feature nodes; heading depth = tree structure. Editing a heading
 * renames the feature; Tab / Shift-Tab indent / outdent it (and its subtree)
 * within the tree; the body under a heading is its description (prose + `@`-refs +
 * marks). Edits settle (debounced) by serializing the WHOLE doc to canonical
 * `tree.codoc` (renderTreeFromDoc) — the host writes it and the existing
 * parse→diff→apply pipeline derives the AMEND / MOVE / ADD / RETIRE ops.
 *
 * Slice 1 is EDITING mode only (changes settle). The Editing/Suggesting toggle and
 * persistent diff decorations land in slices 2–3.
 */
import { Editor, Extension } from '@tiptap/core';
import { TextSelection } from '@tiptap/pm/state';
import { codocExtensions } from './schema';
import { AuthorStamp, AuthorController, REFLECT_META, AUTHOR_META } from './author-plugin';
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
import { GlanceDecorations, GLANCE_UPDATED } from './glance-decorations';
import { CommentDecorations, COMMENTS_UPDATED, resetCommentDecorations } from './comment-decorations';
import { attachHoverCards, HoverCardData } from './hover-card';
import { diffDocsToSuggestions } from '../../state/suggestion-model';
import { renderTreeFromDoc } from '../../state/doc-serialize';
import { mintCommentId, CommentThread } from '../../state/comment-model';
import type { Suggestion } from '../../state/suggestion-model';
import type { PMNode } from '../../state/pm-doc';
import type { FeaturePhase } from '../../state/activity-model';
import type { ThreadsData } from '../protocol';

export type EditMode = 'editing' | 'suggesting';

export interface WholeDocEditorOptions {
    controller: AuthorController;
    getSymbols: () => RefSymbol[];
    /** Editing-mode commit — the whole settled doc (debounced). */
    onSettle: (doc: PMNode) => void;
    /** Suggesting-mode commit — doc-ahead suggestions captured from the edit. */
    onSuggest: (suggestions: Suggestion[]) => void;
    onAccept: (s: Suggestion) => void;
    onReject: (s: Suggestion) => void;
    onWithdraw: (s: Suggestion) => void;
    onOpenBinding: (file: string, symbol: string) => void;
    /** Open a Consult strand link (a description's external `https://` page). */
    onConsult: (url: string) => void;
    /** Create an inline comment: the whole doc (carrying the new anchor mark) + the thread. */
    onCommentCreate: (doc: PMNode, thread: CommentThread) => void;
    /** Edit a comment's body in place. */
    onCommentEdit: (id: string, body: string) => void;
    /** Resolve a comment: the whole doc (anchor mark removed) + the thread id. */
    onCommentResolve: (doc: PMNode, id: string) => void;
    /** Selection moved into a feature — drives tree-pane highlight. */
    onActiveFeature?: (fid: string | null) => void;
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
    /** Per-feature one-line pitches (FeatureMeta.pitch) — feeds glance mode. */
    setPitches: (pitches: Record<string, string>) => void;
    /** Toggle glance mode (collapse each feature to its pitch). Decoration only. */
    setGlance: (on: boolean) => void;
    scrollToFeature: (fid: string) => void;
    isDirty: () => boolean;
    destroy: () => void;
}

const SETTLE_DEBOUNCE_MS = 1200;

/** Signature of the heading sequence (fid:level:retired) — used to detect a
 *  STRUCTURAL change (vs a pure title/description text change). */
function headingSignature(doc: PMNode): string {
    return (doc.content ?? [])
        .filter(b => b.type === 'featureHeading')
        .map(b => {
            const a = (b.attrs ?? {}) as { fid?: string | null; level?: number; retired?: boolean };
            return `${a.fid ?? 'new'}:${a.level ?? 0}:${a.retired ? 1 : 0}`;
        })
        .join('|');
}

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
 *  its description rather than splitting the heading. */
function makeKeymap(): Extension {
    return Extension.create({
        name: 'codocOutlinerKeymap',
        addKeyboardShortcuts() {
            const ed = this.editor;
            return {
                Tab: () => indentHeading(ed),
                'Shift-Tab': () => outdentHeading(ed),
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
    let mode: EditMode = 'editing';      // editing settles; suggesting captures diffs
    // ⌥ momentary override (U2 tail / H3a): when Alt is held during an edit, that settle is
    // captured as a suggestion even in editing mode. Latched at keystroke time because
    // settleNow fires ~1200ms later, long after Alt is released.
    let altDown = false;
    let forceSuggest = false;
    let baselineDoc: PMNode | null = null; // last settled doc (Suggesting diff base)
    let currentSuggestions: Suggestion[] = [];
    let currentThreads: Record<string, ThreadsData> = {};
    let currentPhases: Record<string, FeaturePhase> = {};
    let currentComments: CommentThread[] = [];
    let currentHoverCards: HoverCardData | null = null;
    let currentPitches: Record<string, string> = {}; // B-U2 glance: fid → pitch
    let glanceOn = false;

    const editor = new Editor({
        element: surface,
        extensions: [
            ...codocExtensions(),
            AuthorStamp.configure({ controller: opts.controller, now: () => Date.now() }),
            CodeRefSuggestion.configure({ getSymbols: opts.getSymbols, char: '@' }),
            SuggestionDecorations.configure({
                getSuggestions: () => currentSuggestions,
                handlers: { accept: opts.onAccept, reject: opts.onReject, withdraw: opts.onWithdraw },
            }),
            DependencyDecorations.configure({
                getThreads: () => currentThreads,
                onNavigate: fid => scrollToFeatureInternal(fid, true),
                onOpenBinding: opts.onOpenBinding,
                onConsult: opts.onConsult,
            }),
            ActivityDecorations.configure({ getPhases: () => currentPhases }),
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
            makeKeymap(),
        ],
        content: { type: 'doc', content: [{ type: 'paragraph' }] },
        autofocus: false,
        onUpdate: () => {
            if (suppressUpdate) return;
            dirty = true;
            if (altDown) forceSuggest = true; // ⌥-held edit → suggest this settle (H3a)
            scheduleSettle();
            scheduleRail();
        },
        onSelectionUpdate: () => {
            updateBubble();
            if (!opts.onActiveFeature) return;
            opts.onActiveFeature(activeFid());
        },
    });

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

    function scheduleSettle(): void {
        if (settleTimer) clearTimeout(settleTimer);
        settleTimer = window.setTimeout(settleNow, SETTLE_DEBOUNCE_MS);
        markSaving('saving…');
    }

    function settleNow(): void {
        if (settleTimer) { clearTimeout(settleTimer); settleTimer = 0; }
        if (!dirty) return;
        dirty = false;
        const edited = editor.getJSON() as PMNode;
        // Structural edits (indent/outdent/new/retire — changes to the heading
        // sequence) always settle directly: they can't be expressed as block-level
        // text suggestions, so capturing them in Suggesting mode would silently
        // revert them. Only title/description prose respects Suggesting mode.
        const structural = baselineDoc !== null && headingSignature(edited) !== headingSignature(baselineDoc);
        const suggesting = mode === 'suggesting' || forceSuggest; // visible toggle OR ⌥ override
        forceSuggest = false;
        if (suggesting && baselineDoc && !structural) {
            // Capture the edit as doc-ahead suggestions; DON'T settle the text.
            // Revert the inline edit immediately to the baseline — the change then
            // re-appears as a persistent tracked diff (via the host repost) awaiting
            // the agent. Immediate revert avoids a double-capture if the user keeps
            // typing before the host round-trips.
            const captured = diffDocsToSuggestions(baselineDoc, edited);
            reloadBaseline();
            if (captured.length) opts.onSuggest(captured);
            markSaving('suggested');
        } else {
            opts.onSettle(edited);
            markSaving('saved');
        }
        rebuildRail();
    }

    function reloadBaseline(): void {
        if (!baselineDoc) return;
        suppressUpdate = true;
        try {
            const node = editor.state.schema.nodeFromJSON(baselineDoc);
            if (node.content.size > 0) {
                editor.view.dispatch(
                    editor.state.tr.replaceWith(0, editor.state.doc.content.size, node.content)
                        .setMeta(REFLECT_META, true).setMeta('addToHistory', false),
                );
            }
        } catch { /* leave as-is */ }
        suppressUpdate = false;
    }

    // ── toolbar: per-span authorship + marks + structure ──────────────────────
    // pen/pencil is no longer a future-typing MODE toggle (D2/H2). The human writes
    // in pen by default; this group re-stamps the SELECTED span's authorship ink:
    // "hand to AI" (pencil — AI may revise it directly) / "take back" (pen — committed,
    // AI may only propose). It acts on the selection, like the mark buttons.
    function setSpanMode(mode: 'pen' | 'pencil'): void {
        const { from, to, empty } = editor.state.selection;
        if (empty) { editor.commands.focus(); return; } // needs a selection to re-stamp
        const id = opts.controller.get();
        const markType = editor.state.schema.marks.author;
        if (!markType) return;
        const mark = markType.create({ authorId: id.authorId, role: 'human', mode, ts: Date.now() });
        // removeMark FIRST: the author mark has `excludes: ''` (so distinct-author spans
        // never merge), which means a bare addMark over an already-stamped span would
        // STACK a second author mark instead of replacing it. Strip the old one, then add.
        // Tag AUTHOR_META so the auto-stamp plugin treats this as an explicit re-stamp.
        editor.view.dispatch(
            editor.state.tr.removeMark(from, to, markType).addMark(from, to, mark).setMeta(AUTHOR_META, true),
        );
        editor.commands.focus();
    }
    const authorGrp = document.createElement('div');
    authorGrp.className = 'ce-author';
    authorGrp.append(
        iconButton('✋ to AI', 'Hand this selection to AI — mark it pencil (AI may revise it directly)', () => setSpanMode('pencil'), 'ce-toai'),
        iconButton('↩ take back', 'Take back — mark this selection pen (committed; AI may only propose)', () => setSpanMode('pen'), 'ce-takeback'),
    );

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

    // ── Editing / Suggesting mode (separate from the pen/pencil instrument) ────
    const modeSeg = document.createElement('div');
    modeSeg.className = 'ce-seg ce-modeseg';
    const editBtn = iconButton('Editing', 'Edits settle directly into the tree', () => setEditMode('editing'), 'ce-editmode');
    const sugBtn = iconButton('Suggesting', 'Edits become tracked suggestions for the agent', () => setEditMode('suggesting'), 'ce-sugmode');
    modeSeg.append(editBtn, sugBtn);
    function setEditMode(m: EditMode): void {
        mode = m;
        editBtn.classList.toggle('active', m === 'editing');
        sugBtn.classList.toggle('active', m === 'suggesting');
        wrap.dataset.editmode = m;
        editor.commands.focus();
    }

    const spacer = document.createElement('div');
    spacer.className = 'ce-spacer';
    const saveState = document.createElement('span');
    saveState.className = 'ce-savestate';
    function markSaving(text: string): void { saveState.textContent = text; }

    toolbar.append(modeSeg, marks, authorGrp, structure, spacer, saveState);
    wrap.append(toolbar, body);
    container.append(wrap);

    setEditMode('editing');

    // ── TOC rail + scroll-spy (rehomed scroll indicator) ──────────────────────
    const tickByFid = new Map<string, HTMLElement>();
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
    let muteSpy = false;
    let muteTimer = 0;
    function scrollToFeatureInternal(fid: string, smooth: boolean): void {
        const pos = headingPosForFid(editor, fid);
        if (pos == null) return;
        headingDom(pos)?.scrollIntoView({ block: 'start', behavior: smooth ? 'smooth' : 'auto' });
        // The navigation IS the selection — set it directly and mute the spy briefly
        // so intermediate scroll positions don't flicker-select a neighbour.
        markCurrent(fid);
        muteSpy = true;
        clearTimeout(muteTimer);
        muteTimer = window.setTimeout(() => { muteSpy = false; }, 350);
    }
    function rebuildRail(): void {
        rail.replaceChildren();
        tickByFid.clear();
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
            rail.append(tick);
        });
        updateSpy();
    }
    let spyRaf = 0;
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
            if (current) opts.onActiveFeature?.(current);
        });
    }
    let railTimer = 0;
    function scheduleRail(): void {
        if (railTimer) clearTimeout(railTimer);
        railTimer = window.setTimeout(rebuildRail, 250);
    }
    surface.addEventListener('scroll', updateSpy, { passive: true });

    // ⌥ momentary "suggest this edit" override — track whether Alt is held during input.
    // Listeners live on the editor DOM, so they're torn down with editor.destroy().
    editor.view.dom.addEventListener('keydown', ev => { altDown = ev.altKey; }, true);
    editor.view.dom.addEventListener('keyup', ev => { altDown = ev.altKey; }, true);

    // Code-ref chip click → navigate.
    editor.view.dom.addEventListener('click', ev => {
        const chip = (ev.target as HTMLElement).closest('.codoc-code-ref') as HTMLElement | null;
        if (!chip) return;
        ev.preventDefault();
        opts.onOpenBinding(chip.getAttribute('data-file') || '', chip.getAttribute('data-symbol') || '');
    });

    // Tier-1 hover-preview cards (U4): hover/keyboard a codeRef chip or a feature
    // dependency link → a transient card from the precomputed payload data. Pure
    // overlay; never touches the doc. Torn down in destroy().
    const detachHoverCards = attachHoverCards(editor.view.dom as HTMLElement, {
        getCards: () => currentHoverCards,
        onOpenBinding: opts.onOpenBinding,
        onNavigate: fid => scrollToFeatureInternal(fid, true),
    });

    /** Patch freshly-minted feature ids into the live editor BY INDEX, even while the
     *  user is still editing (dirty). Without this, a new heading stays fid:null in
     *  the editor, so the next settle re-emits a fid-less heading and the pipeline
     *  ADDs a SECOND feature. Conservative: only fills a null fid from the incoming. */
    function patchMintedIds(incoming: PMNode): void {
        const incHeadings = (incoming.content ?? []).filter(b => b.type === 'featureHeading');
        let tr = editor.state.tr;
        let changed = false;
        let idx = -1;
        editor.state.doc.forEach((node, pos) => {
            if (node.type.name !== 'featureHeading') return;
            idx++;
            const incFid = (incHeadings[idx]?.attrs as { fid?: string | null } | undefined)?.fid;
            if (node.attrs.fid == null && incFid) {
                tr = tr.setNodeMarkup(pos, undefined, { ...node.attrs, fid: incFid });
                changed = true;
            }
        });
        if (!changed) return;
        suppressUpdate = true;
        editor.view.dispatch(tr.setMeta(REFLECT_META, true).setMeta('addToHistory', false));
        suppressUpdate = false;
    }

    // ── inline comments — bubble menu + composer + resolve ────────────────────
    // Selecting prose surfaces a one-action bubble (❝); clicking it (or the toolbar
    // ❝) opens a composer aside the selection. Enter saves: a `comment` mark anchors
    // the threadId, the note is handed to the host (→ a `> …` steering line the agent
    // drains). The marker icon + popover live in comment-decorations.ts; resolving
    // removes the anchor mark here and tells the host to drop the `> …` line.
    type ComposeMode = 'create' | 'edit';
    const bubble = document.createElement('div');
    bubble.className = 'ce-cmt-bubble';
    bubble.style.display = 'none';
    const bubbleBtn = document.createElement('button');
    bubbleBtn.type = 'button';
    bubbleBtn.className = 'ce-cmt-bubble-btn';
    bubbleBtn.textContent = '❝ Comment';
    bubbleBtn.title = 'Comment on the selection — a steering note the agent will address';
    bubbleBtn.addEventListener('mousedown', ev => ev.preventDefault()); // keep the editor selection
    bubbleBtn.addEventListener('click', ev => { ev.preventDefault(); openComposerForSelection(); });
    bubble.append(bubbleBtn);
    document.body.append(bubble);

    let composer: HTMLElement | null = null;
    let composeMode: ComposeMode = 'create';
    let composeRange: { from: number; to: number } | null = null;
    let composeFid: string | null = null;
    let composeAnchor = '';
    let composeThreadId = '';

    function selectionRect(): DOMRect | null {
        const { from, to, empty } = editor.state.selection;
        if (empty) return null;
        try {
            const a = editor.view.coordsAtPos(from);
            const b = editor.view.coordsAtPos(to);
            const left = Math.min(a.left, b.left);
            const right = Math.max(a.right, b.right);
            const top = Math.min(a.top, b.top);
            const bottom = Math.max(a.bottom, b.bottom);
            return new DOMRect(left, top, right - left, bottom - top);
        } catch { return null; }
    }

    function updateBubble(): void {
        if (composer) { bubble.style.display = 'none'; return; }
        const { from, to, empty } = editor.state.selection;
        const rect = empty ? null : selectionRect();
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
        const { from, to, empty } = editor.state.selection;
        if (empty || to <= from) { editor.commands.focus(); return; }
        composeMode = 'create';
        composeRange = { from, to };
        composeFid = activeFid();
        composeAnchor = selectedText(from, to);
        composeThreadId = mintCommentId(Date.now(), String(from));
        openComposer(selectionRect(), '');
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

    function coordsRect(from: number, to: number): DOMRect | null {
        try {
            const a = editor.view.coordsAtPos(from);
            const b = editor.view.coordsAtPos(to);
            return new DOMRect(Math.min(a.left, b.left), Math.min(a.top, b.top),
                Math.abs(b.right - a.left), Math.max(a.bottom, b.bottom) - Math.min(a.top, b.top));
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
     *  would drop any UNRELATED pending edit — and, in Suggesting mode, bypass its
     *  capture). suppressUpdate makes onUpdate ignore this transaction, so dirty and
     *  any in-flight settle are left exactly as they were; the host persists the
     *  mark via the comment-* message, not the settle. */
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

    return {
        element: wrap,
        setDoc: (doc: PMNode) => {
            patchMintedIds(doc); // learn minted ids even mid-edit (prevents a double-add)
            if (dirty) return;   // otherwise don't clobber unsettled edits
            // Skip the reload when the SETTLED content (canonical tree.codoc) is
            // unchanged — this is the common case right after a settle round-trips,
            // and reloading would reset the caret to the top + drop local title
            // marks. Only reload when a real content change (e.g. a loop) arrives.
            const sameText = baselineDoc !== null
                && renderTreeFromDoc(doc) === renderTreeFromDoc(editor.getJSON() as PMNode);
            baselineDoc = doc; // the settled baseline Suggesting mode diffs against
            if (sameText) { markSaving(''); return; }

            const keepFid = activeFid();          // stable anchor for existing features
            const keepIndex = activeHeadingIndex(); // fallback for a brand-new (fid:null) heading
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
            const restorePos = (keepFid ? headingPosForFid(editor, keepFid) : null) ?? headingPosAtIndex(keepIndex);
            if (restorePos != null) {
                editor.view.dispatch(editor.state.tr.setSelection(TextSelection.near(editor.state.doc.resolve(restorePos + 1))));
            }
            markSaving('');
            rebuildRail();
        },
        setSuggestions: (list: Suggestion[]) => {
            currentSuggestions = list;
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
        isDirty: () => dirty,
        destroy: () => {
            if (settleTimer) clearTimeout(settleTimer);
            if (railTimer) clearTimeout(railTimer);
            if (muteTimer) clearTimeout(muteTimer);
            if (blurTimer) clearTimeout(blurTimer);
            closeComposer();
            bubble.remove();
            detachHoverCards();        // tear down the hover-card listeners + open card
            resetCommentDecorations(); // tear down the module-level popover + hover timer
            editor.destroy();
        },
    };
}
