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
import { diffDocsToSuggestions } from '../../state/suggestion-model';
import type { Suggestion } from '../../state/suggestion-model';
import type { PMNode } from '../../state/pm-doc';
import type { FeatureDep } from '../protocol';

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
    /** Selection moved into a feature — drives tree-pane highlight. */
    onActiveFeature?: (fid: string | null) => void;
}

export interface WholeDocEditorHandle {
    element: HTMLElement;
    /** Re-seed from an external payload (skipped while the user has unsettled edits). */
    setDoc: (doc: PMNode) => void;
    /** Update the pending diff list (re-renders the inline diff decorations). */
    setSuggestions: (suggestions: Suggestion[]) => void;
    /** Update the per-feature "see also" dependency chips. */
    setDeps: (deps: Record<string, FeatureDep[]>) => void;
    scrollToFeature: (fid: string) => void;
    isDirty: () => boolean;
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
    let baselineDoc: PMNode | null = null; // last settled doc (Suggesting diff base)
    let currentSuggestions: Suggestion[] = [];
    let currentDeps: Record<string, FeatureDep[]> = {};
    let suggestSeq = 0;

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
                getDeps: () => currentDeps,
                onNavigate: fid => scrollToFeatureInternal(fid, true),
            }),
            makeKeymap(),
        ],
        content: { type: 'doc', content: [{ type: 'paragraph' }] },
        autofocus: false,
        onUpdate: () => {
            if (suppressUpdate) return;
            dirty = true;
            scheduleSettle();
            scheduleRail();
        },
        onSelectionUpdate: () => {
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
        // Selection in a description: walk back to the owning heading.
        let fid: string | null = null;
        const here = $from.before(1);
        editor.state.doc.forEach((node, offset) => {
            if (node.type.name === 'featureHeading' && offset <= here) fid = (node.attrs.fid as string) ?? null;
        });
        return fid;
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
        if (mode === 'suggesting' && baselineDoc) {
            // Capture the edit as doc-ahead suggestions; DON'T settle the text.
            // Revert the inline edit immediately to the baseline — the change then
            // re-appears as a persistent tracked diff (via the host repost) awaiting
            // the agent. Immediate revert avoids a double-capture if the user keeps
            // typing before the host round-trips.
            const captured = diffDocsToSuggestions(baselineDoc, edited, i => `d-${suggestSeq++}-${i}`);
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

    // ── toolbar: instrument + marks + structure ───────────────────────────────
    const seg = document.createElement('div');
    seg.className = 'ce-seg';
    const penBtn = iconButton('✒ pen', 'Solid — AI may only propose changes', () => setMode('pen'), 'ce-pen');
    const pencilBtn = iconButton('✏ pencil', 'Tentative — AI may revise directly', () => setMode('pencil'), 'ce-pencil');
    seg.append(penBtn, pencilBtn);
    function setMode(mode: 'pen' | 'pencil'): void {
        opts.controller.setMode(mode);
        penBtn.classList.toggle('active', mode === 'pen');
        pencilBtn.classList.toggle('active', mode === 'pencil');
        wrap.dataset.mode = mode;
        editor.commands.focus();
    }

    const marks = document.createElement('div');
    marks.className = 'ce-marks';
    marks.append(
        iconButton('B', 'Bold (⌘B)', () => editor.chain().focus().toggleBold().run(), 'ce-bold'),
        iconButton('I', 'Italic (⌘I)', () => editor.chain().focus().toggleItalic().run(), 'ce-italic'),
        iconButton('H', 'Highlight', () => editor.chain().focus().toggleHighlight().run(), 'ce-hl'),
        iconButton('❝', 'Comment (ask for a higher-level edit — stored only in slice 1)', () => {
            editor.chain().focus().setMark('comment', { threadId: 'c-' + Date.now().toString(36) }).run();
        }, 'ce-cm'),
    );

    const structure = document.createElement('div');
    structure.className = 'ce-structure';
    structure.append(
        iconButton('＋ feature', 'New feature (sibling)', () => { newFeatureHeading(editor); }, 'ce-new'),
        iconButton('⇥', 'Indent (Tab) — nest under previous sibling', () => { indentHeading(editor); editor.commands.focus(); }, 'ce-indent'),
        iconButton('⇤', 'Outdent (Shift-Tab)', () => { outdentHeading(editor); editor.commands.focus(); }, 'ce-outdent'),
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

    toolbar.append(modeSeg, seg, marks, structure, spacer, saveState);
    wrap.append(toolbar, body);
    container.append(wrap);

    setMode(opts.controller.get().mode);
    setEditMode('editing');

    // ── TOC rail + scroll-spy (rehomed scroll indicator) ──────────────────────
    const tickByFid = new Map<string, HTMLElement>();
    function headingDom(pos: number): HTMLElement | null {
        const dom = editor.view.nodeDOM(pos) as Node | null;
        return dom && dom.nodeType === 1 ? (dom as HTMLElement) : (dom?.parentElement ?? null);
    }
    function scrollToFeatureInternal(fid: string, smooth: boolean): void {
        const pos = headingPosForFid(editor, fid);
        if (pos == null) return;
        headingDom(pos)?.scrollIntoView({ block: 'start', behavior: smooth ? 'smooth' : 'auto' });
    }
    function rebuildRail(): void {
        rail.replaceChildren();
        tickByFid.clear();
        editor.state.doc.forEach((node, pos) => {
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
            const threshold = surface.getBoundingClientRect().top + 72;
            let current: string | null = null;
            editor.state.doc.forEach((node, pos) => {
                if (node.type.name !== 'featureHeading') return;
                const fid = node.attrs.fid as string | null;
                if (!fid) return;
                const dom = headingDom(pos);
                if (dom && dom.getBoundingClientRect().top <= threshold) current = fid;
            });
            surface.querySelectorAll('.codoc-feature-heading.ce-current').forEach(e => e.classList.remove('ce-current'));
            for (const [fid, tick] of tickByFid) tick.classList.toggle('active', fid === current);
            if (current) {
                const pos = headingPosForFid(editor, current);
                if (pos != null) headingDom(pos)?.classList.add('ce-current');
                opts.onActiveFeature?.(current);
            }
        });
    }
    let railTimer = 0;
    function scheduleRail(): void {
        if (railTimer) clearTimeout(railTimer);
        railTimer = window.setTimeout(rebuildRail, 250);
    }
    surface.addEventListener('scroll', updateSpy, { passive: true });

    // Code-ref chip click → navigate.
    editor.view.dom.addEventListener('click', ev => {
        const chip = (ev.target as HTMLElement).closest('.codoc-code-ref') as HTMLElement | null;
        if (!chip) return;
        ev.preventDefault();
        opts.onOpenBinding(chip.getAttribute('data-file') || '', chip.getAttribute('data-symbol') || '');
    });

    return {
        element: wrap,
        setDoc: (doc: PMNode) => {
            if (dirty) return; // don't clobber unsettled edits
            baselineDoc = doc; // the settled baseline Suggesting mode diffs against
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
            markSaving('');
            rebuildRail();
        },
        setSuggestions: (list: Suggestion[]) => {
            currentSuggestions = list;
            editor.view.dispatch(editor.state.tr.setMeta(SUGGESTIONS_UPDATED, true));
        },
        setDeps: (depsMap: Record<string, FeatureDep[]>) => {
            currentDeps = depsMap;
            editor.view.dispatch(editor.state.tr.setMeta(DEPS_UPDATED, true));
        },
        scrollToFeature: (fid: string) => scrollToFeatureInternal(fid, false),
        isDirty: () => dirty,
        destroy: () => {
            if (settleTimer) clearTimeout(settleTimer);
            if (railTimer) clearTimeout(railTimer);
            editor.destroy();
        },
    };
}
