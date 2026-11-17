/**
 * menus.ts: the `/` block menu, the `@` citation picker, and the link field.
 *
 * The menus are one popup: a trigger character typed into a textblock opens a
 * list under the caret, the characters typed after it filter the list, and
 * Enter picks. A pick is a command (commands.ts) or, for `@`, a replace of the
 * typed `@query` by a cite-marked label, which the recorder reads; the menu
 * decides nothing about ops.
 *
 * The link field (Mod-K) is an input over the selection. Enter puts a link on
 * the selected words, or takes it off when the field is empty; the mark change
 * is an ordinary transaction, so the recorder sends it.
 */
import { TextSelection, type EditorState } from '@tiptap/pm/state';
import type { EditorView } from '@tiptap/pm/view';

import type { Projection } from '../calc/projection';
import { deleteFeature, insertSource, setKind, type Env, type Gesture } from './commands';

interface MenuItem { label: string; detail?: string; run: () => void }

export interface Trigger { kind: '/' | '@'; from: number; query: string }

/** The trigger the text before the caret spells, if any. `/` only counts at
 *  the start of a block (a slash mid-sentence is prose), and neither counts
 *  in a source block. */
export function triggerAt(state: EditorState): Trigger | null {
    const { $from, empty } = state.selection;
    if (!empty || $from.depth !== 1 || $from.parent.type.spec.code) return null;
    const before = $from.parent.textBetween(0, $from.parentOffset, '\n');
    const slash = /^\/(\S*)$/.exec(before);
    if (slash) return { kind: '/', from: $from.start(1), query: slash[1] };
    const at = /(?:^|\s)@(\S*)$/.exec(before);
    if (at) return { kind: '@', from: $from.pos - at[1].length - 1, query: at[1] };
    return null;
}

/** Code the tree already cites or binds, filtered by the query; nearest names first. */
export function citeItems(bindings: Projection['bindings'], query: string, limit = 8): Array<{ file: string; symbol: string; label: string }> {
    const q = query.toLowerCase();
    const seen = new Set<string>();
    const out: Array<{ file: string; symbol: string; label: string; rank: number }> = [];
    for (const list of Object.values(bindings.by_feature)) {
        for (const b of list) {
            const key = `${b.file}#${b.symbol}`;
            if (seen.has(key)) continue;
            seen.add(key);
            const label = b.symbol.split('::').pop() || b.file;
            const hay = `${b.symbol} ${b.file}`.toLowerCase();
            const rank = !q ? 1 : label.toLowerCase().startsWith(q) ? 0 : hay.includes(q) ? 1 : -1;
            if (rank >= 0) out.push({ file: b.file, symbol: b.symbol, label, rank });
        }
    }
    return out.sort((a, b) => a.rank - b.rank || a.label.localeCompare(b.label)).slice(0, limit)
        .map(({ file, symbol, label }) => ({ file, symbol, label }));
}

export class Menus {
    private readonly el: HTMLElement;
    private items: MenuItem[] = [];
    private index = 0;

    constructor(private readonly view: EditorView, private readonly env: Env, private readonly projection: () => Projection, parent: HTMLElement) {
        this.el = document.createElement('div');
        this.el.className = 'codoc-menu';
        this.el.hidden = true;
        parent.append(this.el);
    }

    get open(): boolean { return !this.el.hidden; }

    /** Called after every transaction: open, filter or close. */
    update(): void {
        const t = triggerAt(this.view.state);
        if (!t) { this.close(); return; }
        this.items = t.kind === '/' ? this.blockItems(t) : this.citeMenu(t);
        if (!this.items.length) { this.close(); return; }
        this.index = Math.min(this.index, this.items.length - 1);
        this.render();
        const c = this.view.coordsAtPos(this.view.state.selection.from);
        const box = this.el.offsetParent?.getBoundingClientRect() ?? { left: 0, top: 0 };
        this.el.style.left = `${c.left - box.left}px`;
        this.el.style.top = `${c.bottom - box.top + 4}px`;
        this.el.hidden = false;
    }

    /** Arrow keys, Enter and Escape while open. */
    handleKeyDown(ev: KeyboardEvent): boolean {
        if (!this.open) return false;
        if (ev.key === 'ArrowDown') { this.index = (this.index + 1) % this.items.length; this.render(); return true; }
        if (ev.key === 'ArrowUp') { this.index = (this.index - 1 + this.items.length) % this.items.length; this.render(); return true; }
        if (ev.key === 'Enter' || ev.key === 'Tab') { this.items[this.index]?.run(); this.close(); return true; }
        if (ev.key === 'Escape') { this.close(); return true; }
        return false;
    }

    close(): void {
        this.el.hidden = true;
        this.index = 0;
    }

    private render(): void {
        this.el.replaceChildren(...this.items.map((it, i) => {
            const row = document.createElement('div');
            row.className = `codoc-menu-item${i === this.index ? ' is-active' : ''}`;
            row.textContent = it.label;
            if (it.detail) {
                const d = document.createElement('span');
                d.className = 'codoc-menu-detail';
                d.textContent = it.detail;
                row.append(d);
            }
            row.addEventListener('mousedown', ev => { ev.preventDefault(); it.run(); this.close(); });
            return row;
        }));
    }

    private blockItems(t: Trigger): MenuItem[] {
        const marker = t.query.length + 1;
        const item = (label: string, detail: string, g: (state: EditorState) => Gesture | null): MenuItem =>
            ({ label, detail, run: () => { const r = g(this.view.state); if (r) this.view.dispatch(r.tr); } });
        const all: MenuItem[] = [
            ...[1, 2, 3].map(level => item(`Heading ${level}`, `${'#'.repeat(level)} `, s => setKind(s, this.env, 'heading', level, marker))),
            item('Paragraph', 'plain prose', s => setKind(s, this.env, 'paragraph', undefined, marker)),
            item('Bullet list', '- ', s => setKind(s, this.env, 'bullet', undefined, marker)),
            item('Numbered list', '1. ', s => setKind(s, this.env, 'numbered', undefined, marker)),
            item('Table', '| a | b |', s => insertSource(s, this.env, 'table', {}, '', marker)),
            item('Code block', '```', s => insertSource(s, this.env, 'code', {}, '', marker)),
            item('Diagram', '```mermaid', s => insertSource(s, this.env, 'diagram', {}, '', marker)),
        ];
        const caret = this.view.state.selection.$from.parent;
        if (caret.type.name === 'heading') {
            all.push(item('Delete feature', 'its heading and text', s => deleteFeature(s, this.env, caret.attrs.bid as string)));
        }
        const q = t.query.toLowerCase();
        return all.filter(it => !q || it.label.toLowerCase().replace(/\s/g, '').includes(q.replace(/\s/g, '')));
    }

    private citeMenu(t: Trigger): MenuItem[] {
        return citeItems(this.projection().bindings, t.query).map(c => ({
            label: c.label, detail: c.file,
            run: () => {
                const { state } = this.view;
                const to = state.selection.from;
                const mark = state.schema.marks.cite.create({ file: c.file, symbol: c.symbol });
                let tr = state.tr.replaceWith(t.from, to, state.schema.text(c.label, [mark]));
                tr = tr.setSelection(TextSelection.create(tr.doc, t.from + c.label.length)).removeStoredMark(mark);
                this.view.dispatch(tr);
            },
        }));
    }
}

/** The link field. `show` opens it over a selection, prefilled with the link
 *  the selection already has. */
export class LinkInput {
    private readonly el: HTMLElement;
    private readonly input: HTMLInputElement;

    constructor(private readonly view: EditorView, parent: HTMLElement) {
        this.el = document.createElement('div');
        this.el.className = 'codoc-menu codoc-link-input';
        this.el.hidden = true;
        this.input = document.createElement('input');
        this.input.className = 'codoc-find-input';
        this.input.placeholder = 'https://…';
        this.input.setAttribute('aria-label', 'Link address; empty removes the link');
        this.el.append(this.input);
        parent.append(this.el);
        this.input.addEventListener('keydown', ev => {
            if (ev.key === 'Enter') { ev.preventDefault(); this.apply(this.input.value.trim()); }
            else if (ev.key === 'Escape') { ev.preventDefault(); this.close(); }
        });
        this.input.addEventListener('blur', () => { this.el.hidden = true; });
    }

    /** Open over the selection. False when nothing is selected. */
    show(): boolean {
        const { from, to, empty } = this.view.state.selection;
        if (empty) return false;
        const type = this.view.state.schema.marks.link;
        let href = '';
        this.view.state.doc.nodesBetween(from, to, n => { href ||= (type.isInSet(n.marks)?.attrs.href as string | undefined) ?? ''; });
        const c = this.view.coordsAtPos(from);
        const box = this.el.offsetParent?.getBoundingClientRect() ?? { left: 0, top: 0 };
        this.el.style.left = `${c.left - box.left}px`;
        this.el.style.top = `${c.bottom - box.top + 4}px`;
        this.el.hidden = false;
        this.input.value = href;
        this.input.focus();
        this.input.select();
        return true;
    }

    /** Put a link to `raw` on the selected words, or take the link off when it is empty. */
    apply(raw: string): void {
        const { state } = this.view;
        const { from, to, empty } = state.selection;
        const type = state.schema.marks.link;
        if (!empty) {
            // The Markdown form reads only http(s) targets as links.
            const href = raw && !/^https?:\/\//i.test(raw) ? `https://${raw}` : raw;
            const tr = href ? state.tr.addMark(from, to, type.create({ href })) : state.tr.removeMark(from, to, type);
            if (tr.docChanged) this.view.dispatch(tr);
        }
        this.close();
    }

    close(): void {
        this.el.hidden = true;
        this.view.focus();
    }
}
