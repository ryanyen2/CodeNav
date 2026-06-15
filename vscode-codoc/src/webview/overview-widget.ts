/**
 * overview-widget.ts — the concept-first OVERVIEW landing (B-U2 / R2), rendered DOM.
 *
 * Mounts ABOVE the editor (inside the doc surface, NOT inside the TipTap doc) so it
 * scrolls away as a landing and never enters tree.doc.json. Shows the top-theme cards
 * (title + pitch + child count; click → navigate to that feature) and a GROUNDED
 * dependency view — a structured "Theme → depends on → Theme" list built ONLY from the
 * real `feature_edges` carried in `OverviewData.diagramEdges` (Mermaid is not bundled;
 * this is the substitute, with the same "only real edges, no invented arrows" guarantee).
 *
 * Pure presentation: all data comes from the host payload; the only side effects are the
 * `onNavigate` / `onDismiss` callbacks. Colours are `--vscode-*` tokens; no motion is
 * added beyond the shared card chrome (covered by the global reduced-motion gate).
 */
import type { OverviewData, OverviewEdge } from '../state/overview';

export interface OverviewCallbacks {
    /** click a theme card / a diagram theme → scroll the doc to that feature. */
    onNavigate: (fid: string) => void;
    /** dismiss the overview for this workspace (persisted via the host). */
    onDismiss: () => void;
    /** resolve a feature id → title (for the diagram labels of off-card themes). */
    titleOf: (fid: string) => string;
}

function el<K extends keyof HTMLElementTagNameMap>(
    tag: K, cls?: string, text?: string,
): HTMLElementTagNameMap[K] {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
}

/** A compact "kind" label for an edge (call / import …) — the rationale for the arrow. */
function edgeKindLabel(e: OverviewEdge): string {
    const k = (e.kinds ?? []).filter(Boolean);
    if (!k.length) return 'depends on';
    return k.join(' · ');
}

/**
 * Render the overview landing, or `null` when there's nothing to show (no cards) or it
 * was dismissed. The caller inserts the returned element at the top of the doc surface.
 */
export function renderOverview(
    data: OverviewData | undefined,
    dismissed: boolean,
    cb: OverviewCallbacks,
): HTMLElement | null {
    if (!data || data.cards.length === 0 || dismissed) return null;

    const root = el('div', 'ce-overview');
    root.contentEditable = 'false';

    const head = el('div', 'ce-ov-head');
    head.append(el('span', 'ce-ov-title', 'Overview'));
    const sub = el('span', 'ce-ov-sub',
        data.truncated
            ? `${data.cards.length} of ${data.totalThemes} themes`
            : `${data.totalThemes} theme${data.totalThemes === 1 ? '' : 's'}`);
    head.append(sub);
    const spacer = el('div', 'ce-ov-spacer');
    head.append(spacer);
    const dismiss = el('button', 'ce-ov-dismiss', '✕');
    dismiss.title = 'Dismiss the overview for this workspace';
    dismiss.type = 'button';
    dismiss.onclick = () => cb.onDismiss();
    head.append(dismiss);
    root.append(head);

    // ── theme cards ───────────────────────────────────────────────────────────
    const grid = el('div', 'ce-ov-grid');
    for (const c of data.cards) {
        const card = el('button', 'ce-ov-card');
        card.type = 'button';
        card.title = `Go to "${c.title}"`;
        card.onclick = () => cb.onNavigate(c.id);
        card.append(el('span', 'ce-ov-card-title', c.title));
        // Show the pitch only when it adds info beyond the title (fallback ⇒ omit).
        if (c.pitch && c.pitch.trim() && c.pitch.trim() !== c.title.trim()) {
            card.append(el('span', 'ce-ov-card-pitch', c.pitch));
        }
        if (c.childCount > 0) {
            card.append(el('span', 'ce-ov-card-count',
                `${c.childCount} sub-feature${c.childCount === 1 ? '' : 's'}`));
        }
        grid.append(card);
    }
    root.append(grid);

    // ── grounded dependency view (Mermaid substitute) ──────────────────────────
    // Only when ≥ 2 top themes are connected by a real edge; a fixed-height scroll box.
    if (data.showDiagram && data.diagramEdges.length) {
        const diag = el('div', 'ce-ov-diagram');
        diag.append(el('div', 'ce-ov-diagram-label', 'How the themes connect'));
        const list = el('div', 'ce-ov-diagram-list');
        for (const e of data.diagramEdges) {
            const fromTitle = cb.titleOf(e.from) || '(theme)';
            const toTitle = cb.titleOf(e.to) || '(theme)';
            const row = el('div', 'ce-ov-edge');
            const a = el('button', 'ce-ov-edge-node', fromTitle);
            a.type = 'button';
            a.onclick = () => cb.onNavigate(e.from);
            const arrow = el('span', 'ce-ov-edge-arrow', '→');
            arrow.title = edgeKindLabel(e);
            const b = el('button', 'ce-ov-edge-node', toTitle);
            b.type = 'button';
            b.onclick = () => cb.onNavigate(e.to);
            const kind = el('span', 'ce-ov-edge-kind', edgeKindLabel(e));
            row.append(a, arrow, b, kind);
            list.append(row);
        }
        diag.append(list);
        root.append(diag);
    }

    return root;
}
