/**
 * find.ts — Find and Replace over the document.
 *
 * Matching is over each textblock's text; matches never cross a block. A
 * replacement is an ordinary `insertText` transaction, so the recorder turns it
 * into a delete of the matched identities and an insert by the person: nothing
 * here knows about ops. Highlights are decorations from a small plugin.
 */
import type { Node as PMNode } from '@tiptap/pm/model';
import { Plugin, PluginKey, TextSelection } from '@tiptap/pm/state';
import { Decoration, DecorationSet, type EditorView } from '@tiptap/pm/view';

export interface Match { from: number; to: number }

const findKey = new PluginKey<FindState>('codoc-find');

interface FindState { query: string; current: number; matches: Match[] }

/** Every case-insensitive occurrence of `query` inside single textblocks. */
export function findMatches(doc: PMNode, query: string): Match[] {
    const out: Match[] = [];
    if (!query) return out;
    const q = query.toLowerCase();
    doc.forEach((block, pos) => {
        const text = block.textBetween(0, block.content.size, '\n').toLowerCase();
        let i = text.indexOf(q);
        while (i >= 0) {
            out.push({ from: pos + 1 + i, to: pos + 1 + i + q.length });
            i = text.indexOf(q, i + Math.max(1, q.length));
        }
    });
    return out;
}

export function findPlugin(): Plugin<FindState> {
    return new Plugin<FindState>({
        key: findKey,
        state: {
            init: () => ({ query: '', current: 0, matches: [] }),
            apply(tr, prev) {
                const meta = tr.getMeta(findKey) as Partial<FindState> | undefined;
                const query = meta?.query ?? prev.query;
                const matches = meta?.query !== undefined || tr.docChanged ? findMatches(tr.doc, query) : prev.matches;
                const current = meta?.current ?? Math.min(prev.current, Math.max(0, matches.length - 1));
                return { query, current, matches };
            },
        },
        props: {
            decorations(state) {
                const s = findKey.getState(state);
                if (!s || !s.matches.length) return null;
                return DecorationSet.create(state.doc, s.matches.map((m, i) =>
                    Decoration.inline(m.from, m.to, { class: `codoc-find-match${i === s.current ? ' is-current' : ''}` })));
            },
        },
    });
}

/** The widget: one input, a count, prev/next, and (when asked) a replace row. */
export class FindWidget {
    readonly el: HTMLElement;
    private readonly input: HTMLInputElement;
    private readonly repl: HTMLInputElement;
    private readonly count: HTMLElement;
    private readonly replRow: HTMLElement;

    constructor(private readonly view: EditorView, parent: HTMLElement) {
        this.el = document.createElement('div');
        this.el.className = 'codoc-find';
        this.el.hidden = true;
        this.input = mk('input', 'codoc-find-input', this.el) as HTMLInputElement;
        this.input.placeholder = 'Find';
        this.count = mk('span', 'codoc-find-count', this.el);
        btn('‹', 'previous', this.el, () => this.step(-1));
        btn('›', 'next', this.el, () => this.step(1));
        btn('×', 'close', this.el, () => this.close());
        this.replRow = mk('div', 'codoc-find-replace', this.el);
        this.repl = mk('input', 'codoc-find-input', this.replRow) as HTMLInputElement;
        this.repl.placeholder = 'Replace';
        btn('Replace', 'replace the current match', this.replRow, () => this.replace(false));
        btn('All', 'replace every match', this.replRow, () => this.replace(true));
        this.input.addEventListener('input', () => this.set(this.input.value, 0));
        this.el.addEventListener('keydown', ev => {
            if (ev.key === 'Escape') { ev.preventDefault(); this.close(); }
            else if (ev.key === 'Enter') { ev.preventDefault(); ev.target === this.repl ? this.replace(false) : this.step(ev.shiftKey ? -1 : 1); }
        });
        parent.append(this.el);
    }

    open(replace: boolean): void {
        this.el.hidden = false;
        this.replRow.hidden = !replace;
        const sel = this.view.state.selection;
        if (!sel.empty) this.input.value = this.view.state.doc.textBetween(sel.from, sel.to);
        this.set(this.input.value, 0);
        this.input.focus();
        this.input.select();
    }

    close(): void {
        this.el.hidden = true;
        this.set('', 0);
        this.view.focus();
    }

    private state(): FindState { return findKey.getState(this.view.state)!; }

    private set(query: string, current: number): void {
        this.view.dispatch(this.view.state.tr.setMeta(findKey, { query, current }));
        this.paint();
    }

    private paint(): void {
        const s = this.state();
        this.count.textContent = s.query ? (s.matches.length ? `${s.current + 1} of ${s.matches.length}` : 'none') : '';
        const m = s.matches[s.current];
        if (m) this.view.dispatch(this.view.state.tr.setSelection(TextSelection.create(this.view.state.doc, m.from, m.to)).scrollIntoView());
    }

    private step(d: number): void {
        const s = this.state();
        if (!s.matches.length) return;
        this.set(s.query, (s.current + d + s.matches.length) % s.matches.length);
    }

    private replace(all: boolean): void {
        const s = this.state();
        if (!s.matches.length) return;
        const targets = all ? [...s.matches].reverse() : [s.matches[s.current]];
        let tr = this.view.state.tr;
        for (const m of targets) tr = tr.insertText(this.repl.value, m.from, m.to);
        this.view.dispatch(tr);
        this.set(s.query, all ? 0 : Math.min(s.current, Math.max(0, this.state().matches.length - 1)));
    }
}

function mk<K extends keyof HTMLElementTagNameMap>(tag: K, cls: string, parent: HTMLElement): HTMLElementTagNameMap[K] {
    const e = document.createElement(tag);
    e.className = cls;
    parent.append(e);
    return e;
}

function btn(label: string, title: string, parent: HTMLElement, onClick: () => void): void {
    const b = mk('button', 'codoc-btn codoc-find-btn', parent);
    b.textContent = label;
    b.title = title;
    b.addEventListener('click', onClick);
}
