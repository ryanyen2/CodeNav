/**
 * ui-state.ts — pure (de)serialization of the webview's per-editor UI state (U5).
 *
 * Persisted via `vscode.getState()/setState()` so a close→reopen or a full window reload
 * restores selection, expansion, caret, and scroll instead of resetting to expand-all + first
 * root. Webview-local and DOM-free → unit-testable; the restore wiring lives in doc-view.ts.
 *
 * Versioned (`v`) and tolerant: an unrecognized / legacy / null prior state deserializes to
 * `null` (the caller falls back to defaults), and a partial v:1 state fills safe defaults — so
 * a format bump or a corrupt blob never throws and never restores garbage.
 */
export interface UiState {
    v: 1;
    selectedId: string | null;
    expanded: string[];
    caretPos: number;
    treeScroll: number;
    docScroll: number;
    /** Nav-tree column width in px (0 = stylesheet default). Survives reload (continuous resize). */
    treeWidth: number;
    /** Focus mode: dim tree rows unrelated to the focused feature. ON by default. */
    focusMode: boolean;
}

export function serializeUiState(s: Omit<UiState, 'v'>): UiState {
    return {
        v: 1,
        selectedId: s.selectedId,
        expanded: s.expanded,
        caretPos: s.caretPos,
        treeScroll: s.treeScroll,
        docScroll: s.docScroll,
        treeWidth: s.treeWidth,
        focusMode: s.focusMode,
    };
}

const num = (x: unknown): number => (typeof x === 'number' && Number.isFinite(x) ? x : 0);

/** Parse a persisted blob → UiState, or null when there's nothing valid to restore. */
export function deserializeUiState(raw: unknown): UiState | null {
    if (!raw || typeof raw !== 'object') return null;
    const o = raw as Record<string, unknown>;
    if (o.v !== 1) return null; // unknown / legacy version → no restore (caller defaults)
    return {
        v: 1,
        selectedId: typeof o.selectedId === 'string' ? o.selectedId : null,
        expanded: Array.isArray(o.expanded) ? o.expanded.filter((x): x is string => typeof x === 'string') : [],
        caretPos: num(o.caretPos),
        treeScroll: num(o.treeScroll),
        docScroll: num(o.docScroll),
        treeWidth: num(o.treeWidth),
        focusMode: o.focusMode !== false,   // default ON; only an explicit false disables it
    };
}
