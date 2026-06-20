/**
 * palette-view.ts — the ⌘K command palette UI (P4 / spec §D). A centered floating card over a
 * calm scrim, reusing the existing popover chrome (--shadow-pop, 10px radius, ce-pop-in). It
 * owns its DOM, keyboard (↑/↓ move, ↵ run, ⇧↵ secondary, Esc close), and the live fuzzy-ranked
 * render with match highlights. All decisions are delegated to the pure palette.ts; this file
 * is the EDH-only shell. No new host messages — the runner maps each action to existing
 * webview callbacks (§D.4).
 */
import { icon, IconName } from './icons';
import { prefersReducedMotion } from './motion';
import {
    PaletteItem, PaletteContext, MatchSpan, rankItems, buildActions, buildFeatureItems,
    welcomeItems, createFeatureItem, sectionLabel,
} from './palette';

/** Everything the palette needs from doc-view: the live context snapshot, the recent-feature
 *  list, and the action runner (interprets a PaletteItem's `action`+`arg`, `shift` = ⇧↵). */
export interface PaletteHost {
    context: () => PaletteContext;
    recentFids: () => string[];
    run: (item: PaletteItem, shift: boolean) => void;
}

export class CommandPalette {
    private root: HTMLElement | null = null;
    private input: HTMLInputElement | null = null;
    private list: HTMLElement | null = null;
    private rows: { el: HTMLElement; item: PaletteItem; shiftable: boolean }[] = [];
    private active = 0;

    constructor(private readonly host: PaletteHost) {}

    get isOpen(): boolean { return this.root !== null; }

    toggle(): void { this.isOpen ? this.close() : this.open(); }

    open(): void {
        if (this.root) return;
        const scrim = document.createElement('div');
        scrim.className = 'ce-palette-scrim';
        scrim.addEventListener('mousedown', e => { if (e.target === scrim) this.close(); });

        const card = document.createElement('div');
        card.className = 'ce-palette';
        if (prefersReducedMotion()) card.style.animation = 'none'; // §D.4: instant show under reduced motion

        const inputRow = document.createElement('div');
        inputRow.className = 'ce-palette-input';
        inputRow.append(icon('magnifying-glass', { className: 'ce-palette-search' }));
        this.input = document.createElement('input');
        this.input.type = 'text';
        this.input.placeholder = 'Search features, run a command…';
        this.input.spellcheck = false;
        this.input.addEventListener('input', () => this.render());
        inputRow.append(this.input);
        const hint = document.createElement('span');
        hint.className = 'ce-palette-hint';
        hint.textContent = '↵ go · ⇧↵ open code';
        inputRow.append(hint);

        this.list = document.createElement('div');
        this.list.className = 'ce-palette-list';

        card.append(inputRow, this.list);
        scrim.append(card);
        document.body.append(scrim);
        this.root = scrim;
        this.input.focus();
        this.render();
    }

    close(): void {
        this.root?.remove();
        this.root = this.input = this.list = null;
        this.rows = [];
        this.active = 0;
    }

    /** Handle a keydown while the palette is open. Returns true if it consumed the event. */
    onKeydown(e: KeyboardEvent): boolean {
        if (!this.root) return false;
        switch (e.key) {
            case 'Escape': e.preventDefault(); this.close(); return true;
            case 'ArrowDown': e.preventDefault(); this.move(1); return true;
            case 'ArrowUp': e.preventDefault(); this.move(-1); return true;
            case 'Enter': e.preventDefault(); this.runActive(e.shiftKey); return true;
            default: return false;
        }
    }

    // ── render ───────────────────────────────────────────────────────────────
    private render(): void {
        if (!this.list || !this.input) return;
        const query = this.input.value;
        const ctx = this.host.context();
        const items = this.assemble(query, ctx);
        this.list.replaceChildren();
        this.rows = [];

        if (items.length === 0) {
            // §D.3 no-match: a calm line + the create-feature affordance.
            const empty = document.createElement('div');
            empty.className = 'cr-empty';
            empty.textContent = `No features or commands match "${query}"`;
            this.list.append(empty);
            const create = createFeatureItem(query);
            if (create) this.appendRow(create, [], true);
        } else {
            let lastSection: PaletteItem['section'] | null = null;
            for (const { item, spans } of items) {
                if (item.section !== lastSection) {
                    lastSection = item.section;
                    const label = document.createElement('div');
                    label.className = 'ce-peek-label';
                    label.textContent = sectionLabel(item.section);
                    this.list.append(label);
                }
                this.appendRow(item, spans);
            }
        }
        this.active = 0;
        this.highlightActive();
    }

    /** Assemble + rank the rows for `query` (§D.2/§D.3). Empty query → the welcome dashboard +
     *  the always-present actions; a query → fuzzy-ranked features + matching action titles. */
    private assemble(query: string, ctx: PaletteContext): { item: PaletteItem; spans: MatchSpan[] }[] {
        const actions = buildActions(ctx);
        if (!query.trim()) {
            // welcome dashboard first, then the contextual actions (a status board + a legend).
            const welcome = welcomeItems(ctx, this.host.recentFids()).map(item => ({ item, spans: [] as MatchSpan[] }));
            return [...welcome, ...actions.map(item => ({ item, spans: [] as MatchSpan[] }))];
        }
        const features = rankItems(query, buildFeatureItems(ctx), f => f.title);
        const matchedActions = rankItems(query, actions, a => a.title);
        return [...features, ...matchedActions];
    }

    private appendRow(item: PaletteItem, spans: MatchSpan[], isCreate = false): void {
        if (!this.list) return;
        const row = document.createElement('div');
        row.className = 'ce-palette-row';
        const lead = document.createElement('span');
        lead.className = 'ce-palette-icon';
        if (item.icon) lead.append(icon(item.icon as IconName));
        row.append(lead);
        const title = document.createElement('span');
        title.className = 'ce-palette-title';
        title.append(...highlightSpans(item.title, spans));
        row.append(title);
        if (item.detail) {
            const detail = document.createElement('span');
            detail.className = 'ce-palette-detail';
            detail.textContent = item.detail;
            row.append(detail);
        }
        const idx = this.rows.length;
        row.addEventListener('mousedown', e => { e.preventDefault(); this.active = idx; this.runActive(e.shiftKey); });
        row.addEventListener('mousemove', () => { if (this.active !== idx) { this.active = idx; this.highlightActive(); } });
        this.list.append(row);
        this.rows.push({ el: row, item, shiftable: !!item.hasSecondary || isCreate });
    }

    private move(delta: number): void {
        if (!this.rows.length) return;
        this.active = (this.active + delta + this.rows.length) % this.rows.length;
        this.highlightActive();
    }

    private highlightActive(): void {
        this.rows.forEach((r, i) => r.el.classList.toggle('active', i === this.active));
        this.rows[this.active]?.el.scrollIntoView({ block: 'nearest' });
    }

    private runActive(shift: boolean): void {
        const row = this.rows[this.active];
        if (!row) return;
        this.host.run(row.item, shift && row.shiftable);
        this.close();
    }
}

/** Split `text` into alternating plain + matched (highlighted) nodes for the --accent bold
 *  highlight (§D.1). Pure DOM construction; the spans come from the fuzzy matcher. */
function highlightSpans(text: string, spans: MatchSpan[]): Node[] {
    if (!spans.length) return [document.createTextNode(text)];
    const out: Node[] = [];
    let cursor = 0;
    for (const s of spans) {
        if (s.start > cursor) out.push(document.createTextNode(text.slice(cursor, s.start)));
        const mark = document.createElement('span');
        mark.className = 'ce-palette-match';
        mark.textContent = text.slice(s.start, s.end);
        out.push(mark);
        cursor = s.end;
    }
    if (cursor < text.length) out.push(document.createTextNode(text.slice(cursor)));
    return out;
}
