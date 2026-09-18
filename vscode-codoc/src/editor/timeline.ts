/**
 * timeline.ts — the History scrubber (09-editor §7): a read-only reconstruction
 * of the page at a position in the window, with that moment's change marked.
 * It never touches the live editor: pushing Tuesday's prose through the
 * recorder would record the author reverting the tree.
 */
import type { Projection } from '../calc/projection';
import { shapeAt, textAt, wordDiff, type HistoryWindow } from '../calc/window';

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
        // "At now" is a position the person chose, not a number. A new op makes
        // the old number a moment in the PAST, so reading it back flipped the
        // page into a reconstruction nobody asked for, with the live editor
        // hidden under it — every time anyone else edited anything.
        const atNow = Number(this.range.value) >= (this.window?.ops.length ?? 0);
        this.window = w;
        this.proj = proj;
        this.range.max = String(w.ops.length);
        if (atNow) { this.range.value = String(w.ops.length); this.show(w.ops.length); }
        else if (!this.bar.hidden) this.show(Number(this.range.value));
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
        // Every block the page had then: the live ones, plus any the window
        // names that have since been deleted or merged away, in that order.
        const live = new Map(proj.blocks.filter(b => b.kind !== 'comment').map(b => [b.id, b]));
        const gone = new Set<string>();
        for (const entry of w.ops) for (const bid of Object.keys(entry.blocks)) if (!live.has(bid)) gone.add(bid);
        const shape = shapeAt(w, p);
        const rankOf = (id: string) => shape.rank.get(id) ?? live.get(id)?.rank ?? id;
        // Ranks are base-62 in ASCII order (`rank.py`), so they compare by code
        // point. `localeCompare` collates case-insensitively — 'a' before 'V' —
        // and the sixth appended block and every one after it would sort above
        // the first five on every past page.
        const order = [...live.keys(), ...gone].sort((a, b2) => (rankOf(a) < rankOf(b2) ? -1 : rankOf(a) > rankOf(b2) ? 1 : 0));
        for (const bid of order) {
            if (shape.dead.has(bid)) continue;              // not on the page then
            const was = shape.shape.get(bid);
            const b = live.get(bid) ?? { id: bid, kind: was?.kind ?? 'paragraph', level: was?.level ?? 0,
                                         rank: '', head: 0, fields: {} };
            const f = b.fields.text as { text?: string } | undefined;
            const now = textAt(w, bid, p, f?.text ?? '');
            if (now === null) continue;
            const el = document.createElement(b.kind === 'heading' ? 'div' : 'p');
            el.className = b.kind === 'heading' ? `codoc-heading codoc-h${b.level}` : 'codoc-paragraph';
            if (!live.has(bid)) el.classList.add('codoc-past-gone');
            if (bid in e.blocks) {
                const was = (p > 0 && shapeAt(w, p - 1).dead.has(bid)) ? '' : (textAt(w, bid, p - 1, f?.text ?? '') ?? '');
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
