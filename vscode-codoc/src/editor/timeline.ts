/**
 * timeline.ts — the History scrubber (09-editor §7): a read-only reconstruction
 * of the page at a position in the window, with that moment's change marked.
 * It never touches the live editor: pushing Tuesday's prose through the
 * recorder would record the author reverting the tree.
 */
import type { Projection } from '../calc/projection';
import { textAt, wordDiff, type HistoryWindow } from '../calc/window';

export class Timeline {
    readonly bar: HTMLElement;
    readonly past: HTMLElement;
    private readonly range: HTMLInputElement;
    private readonly caption: HTMLElement;
    private window: HistoryWindow | null = null;
    private proj: Projection | null = null;

    constructor(parent: HTMLElement, private readonly editorDom: () => HTMLElement | null) {
        this.bar = document.createElement('div');
        this.bar.className = 'codoc-timeline';
        this.bar.hidden = true;
        this.range = document.createElement('input');
        this.range.type = 'range';
        this.range.min = '0';
        this.caption = document.createElement('span');
        this.caption.className = 'codoc-timeline-caption';
        this.bar.append(this.range, this.caption);
        this.past = document.createElement('div');
        this.past.className = 'codoc-past';
        this.past.hidden = true;
        parent.prepend(this.bar);
        parent.append(this.past);
        this.range.addEventListener('input', () => this.show(Number(this.range.value)));
    }

    setWindow(w: HistoryWindow, proj: Projection): void {
        this.window = w;
        this.proj = proj;
        this.range.max = String(w.ops.length);
        if (!this.bar.hidden && Number(this.range.value) < w.ops.length) this.show(Number(this.range.value));
        else this.range.value = String(w.ops.length);
    }

    toggle(on: boolean): void {
        this.bar.hidden = !on;
        if (!on) this.show(this.window?.ops.length ?? 0);
    }

    /** Position `p` = after the p-th op of the window; `p == ops.length` is now. */
    private show(p: number): void {
        const w = this.window, proj = this.proj, dom = this.editorDom();
        if (!w || !proj || !dom) return;
        if (p >= w.ops.length) {
            this.past.hidden = true;
            dom.hidden = false;
            this.caption.textContent = 'now';
            return;
        }
        const e = w.ops[p];
        this.caption.textContent = `${e.actor.replace(/^human:/, '')} · ${e.kind} · ${e.at.slice(0, 20)}`;
        this.past.replaceChildren();
        for (const b of proj.blocks) {
            if (b.kind === 'comment') continue;
            const f = b.fields.text as { text?: string } | undefined;
            const now = textAt(w, b.id, p, f?.text ?? '');
            if (now === null) continue;
            const el = document.createElement(b.kind === 'heading' ? 'div' : 'p');
            el.className = b.kind === 'heading' ? `codoc-heading codoc-h${b.level}` : 'codoc-paragraph';
            if (b.id in e.blocks) {
                const was = textAt(w, b.id, p - 1, f?.text ?? '') ?? '';
                for (const [k, t] of wordDiff(was, now)) {
                    const span = document.createElement(k === 'eq' ? 'span' : k);
                    span.textContent = t;
                    el.append(span);
                }
            } else {
                el.textContent = now;
            }
            this.past.append(el);
        }
        dom.hidden = true;
        this.past.hidden = false;
    }
}
