/**
 * editor.ts — mounts the TipTap block editor that replaces the per-section
 * description `<textarea>` (U3). One editor per active description edit, seeded
 * from the authoritative doc's paragraph blocks (so authorship marks survive),
 * with an `@`-autocomplete for code refs (U5), per-span authorship stamping +
 * a pen/pencil toggle (U6), and basic marks (U7).
 *
 * The editor is the rich surface; on commit it hands back paragraph blocks (with
 * marks) — the host persists them to `tree.doc.json` and derives the `tree.codoc`
 * description text. Webview-only (imports `@tiptap/*` + the DOM).
 */
import { Editor, JSONContent } from '@tiptap/core';
import { codocExtensions } from './schema';
import { AuthorStamp, AuthorController } from './author-plugin';
import { CodeRefSuggestion, RefSymbol } from './code-ref-suggestion';
import type { AuthorMode, PMNode } from '../../state/pm-doc';

export interface DescEditorOptions {
    /** Paragraph blocks to seed (from the authoritative doc for this feature). */
    blocks: PMNode[];
    controller: AuthorController;
    getSymbols: () => RefSymbol[];
    onCommit: (blocks: PMNode[]) => void;
    onCancel: () => void;
    onOpenBinding: (file: string, symbol: string) => void;
}

export interface DescEditorHandle {
    element: HTMLElement;
    commit: () => void;
    destroy: () => void;
}

function iconButton(label: string, title: string, onClick: () => void, extraClass = ''): HTMLButtonElement {
    const b = document.createElement('button');
    b.className = ('ce-btn ' + extraClass).trim();
    b.textContent = label;
    b.title = title;
    b.type = 'button';
    b.addEventListener('mousedown', ev => ev.preventDefault()); // keep editor selection
    b.addEventListener('click', ev => { ev.preventDefault(); onClick(); });
    return b;
}

export function mountDescriptionEditor(opts: DescEditorOptions): DescEditorHandle {
    const wrap = document.createElement('div');
    wrap.className = 'codoc-editor';

    const toolbar = document.createElement('div');
    toolbar.className = 'ce-toolbar';

    const surface = document.createElement('div');
    surface.className = 'ce-surface';

    const content: JSONContent = {
        type: 'doc',
        content: (opts.blocks.length ? opts.blocks : [{ type: 'paragraph' }]) as JSONContent[],
    };

    const editor = new Editor({
        element: surface,
        extensions: [
            ...codocExtensions(),
            AuthorStamp.configure({ controller: opts.controller, now: () => Date.now() }),
            CodeRefSuggestion.configure({ getSymbols: opts.getSymbols, char: '@' }),
        ],
        content,
        autofocus: 'end',
        editorProps: {
            handleKeyDown: (_view, event) => {
                if (event.key === 'Escape') { event.preventDefault(); opts.onCancel(); return true; }
                if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); commit(); return true; }
                return false;
            },
        },
    });

    function commit(): void {
        const json = editor.getJSON() as PMNode;
        opts.onCommit(json.content ?? []);
    }

    // ── pen / pencil segmented toggle (drives the active authorship mode) ──────
    const seg = document.createElement('div');
    seg.className = 'ce-seg';
    const penBtn = iconButton('✒ pen', 'Solid — AI may only propose changes (pen)', () => setMode('pen'), 'ce-pen');
    const pencilBtn = iconButton('✏ pencil', 'Tentative — AI may revise directly (pencil)', () => setMode('pencil'), 'ce-pencil');
    seg.append(penBtn, pencilBtn);
    function setMode(mode: AuthorMode): void {
        opts.controller.setMode(mode);
        penBtn.classList.toggle('active', mode === 'pen');
        pencilBtn.classList.toggle('active', mode === 'pencil');
        wrap.dataset.mode = mode;
        editor.commands.focus();
    }
    setMode(opts.controller.get().mode);

    // ── inline marks (U7) ─────────────────────────────────────────────────────
    const marks = document.createElement('div');
    marks.className = 'ce-marks';
    marks.append(
        iconButton('B', 'Bold (⌘B)', () => editor.chain().focus().toggleBold().run(), 'ce-bold'),
        iconButton('I', 'Italic (⌘I)', () => editor.chain().focus().toggleItalic().run(), 'ce-italic'),
        iconButton('H', 'Highlight', () => editor.chain().focus().toggleHighlight().run(), 'ce-hl'),
        iconButton('❝', 'Comment — ask for a higher-level edit (stored only in Phase 1)', () => {
            const threadId = 'c-' + Date.now().toString(36);
            editor.chain().focus().setMark('comment', { threadId }).run();
        }, 'ce-cm'),
    );

    const spacer = document.createElement('div');
    spacer.className = 'ce-spacer';

    const done = iconButton('Done', 'Commit (⌘↵)', () => commit(), 'ce-done primary');
    const cancel = iconButton('Cancel', 'Discard (Esc)', () => opts.onCancel(), 'ce-cancel');

    toolbar.append(seg, marks, spacer, cancel, done);
    wrap.append(toolbar, surface);

    // Code-ref chip click → navigate to the bound symbol.
    editor.view.dom.addEventListener('click', ev => {
        const chip = (ev.target as HTMLElement).closest('.codoc-code-ref') as HTMLElement | null;
        if (!chip) return;
        ev.preventDefault();
        opts.onOpenBinding(chip.getAttribute('data-file') || '', chip.getAttribute('data-symbol') || '');
    });

    return {
        element: wrap,
        commit,
        destroy: () => editor.destroy(),
    };
}
