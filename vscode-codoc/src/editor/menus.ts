/**
 * menus.ts — the `/` block menu and the `@` citation picker (09-editor §8).
 *
 * Both are one popup: a trigger character typed into a textblock opens a list
 * under the caret, the characters typed after it filter the list, and Enter
 * picks. A pick is an ordinary transaction (a command for `/`, a replace of
 * the typed `@query` by a cite-marked label for `@`), so the recorder and the
 * commands do the recording; the menu decides nothing about ops.
 */
import { TextSelection, type EditorState } from '@tiptap/pm/state';
import type { EditorView } from '@tiptap/pm/view';

import type { Projection } from '../calc/projection';
import { makeHeading, makeParagraph, type Env } from './commands';

export interface MenuItem { label: string; detail?: string; run: () => void }

export interface Trigger { kind: '/' | '@'; from: number; query: string }

/** The trigger the text before the caret spells, if any. `/` only counts at
 *  the start of a block (a slash mid-sentence is prose). */
export function triggerAt(state: EditorState): Trigger | null {
    const { $from, empty } = state.selection;
    if (!empty || $from.depth !== 1) return null;
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
    private trigger: Trigger | null = null;

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
        this.trigger = t;
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
        this.trigger = null;
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
        const all: MenuItem[] = [1, 2, 3].map(level => ({
            label: `Heading ${level}`, detail: `${'#'.repeat(level)} `,
            run: () => { const g = makeHeading(this.view.state, this.env, level, marker); if (g) this.view.dispatch(g.tr); },
        }));
        all.push({ label: 'Paragraph', detail: 'plain prose', run: () => { const g = makeParagraph(this.view.state, this.env, marker); if (g) this.view.dispatch(g.tr); } });
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
