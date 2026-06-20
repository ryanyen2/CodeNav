/**
 * palette.ts — the pure logic of the ⌘K command palette (P4 / spec §D).
 *
 * Holds the deterministic, DOM-free pieces so they are unit-testable: the tiny no-dep fuzzy
 * matcher (contiguous-run + word-boundary ranking) with match-highlight spans, the contextual
 * command-set assembly from payload state, and the empty/welcome/no-match selection. The DOM
 * (the floating card, scrim, keyboard nav, render) lives in palette-view.ts and is EDH-only.
 *
 * No new host messages: every action maps to an existing `vscode.postMessage` kind or a webview
 * navigation callback (§D.4); the action ids are interpreted by the view's dispatcher.
 */
import type { IconName } from './icons';

/** A palette row. `kind` groups it under a section header; `icon` is the leading glyph (a
 *  feature status glyph or an action's C-icon, so the palette doubles as the lifecycle legend).
 *  `action` is the dispatch tag the view's runner interprets; `arg` carries a fid/eventId/etc. */
export interface PaletteItem {
    id: string;
    section: 'features' | 'actions' | 'recent' | 'quick' | 'view';
    title: string;
    detail?: string;
    icon?: IconName;
    /** the dispatch tag (e.g. 'goto', 'accept-all', 'hand-off', 'open-code', 'create') */
    action: string;
    /** action payload (a feature id, an event-id list, the create title, …) */
    arg?: string;
    /** whether ⇧↵ has a distinct secondary action (e.g. goto → open bound code) */
    hasSecondary?: boolean;
}

/** A char span [start,end) of `text` that matched the query — for the --accent bold highlight. */
export interface MatchSpan { start: number; end: number }

/** A fuzzy match result: the total score (higher = better) + the matched char spans. */
export interface FuzzyResult { score: number; spans: MatchSpan[] }

/**
 * A tiny no-dep subsequence fuzzy matcher (§D.1). Returns null when `query` isn't a
 * subsequence of `text` (case-insensitive). Score rewards: a contiguous run (each char
 * adjacent to the previous +8), a word-boundary start (start-of-string / after a separator
 * +6), and an early first match (− the first index). This ranks "loop" → "Loop A" above
 * "deveLOOPment" without a dependency. Spans coalesce adjacent matched chars.
 */
export function fuzzyMatch(query: string, text: string): FuzzyResult | null {
    if (!query) return { score: 0, spans: [] };
    const q = query.toLowerCase();
    const t = text.toLowerCase();
    const spans: MatchSpan[] = [];
    let score = 0;
    let ti = 0;
    let prevMatch = -2;            // the index of the previously matched char
    let firstIndex = -1;
    for (let qi = 0; qi < q.length; qi++) {
        const ch = q[qi];
        let found = -1;
        for (let i = ti; i < t.length; i++) {
            if (t[i] === ch) { found = i; break; }
        }
        if (found === -1) return null;   // not a subsequence
        if (firstIndex === -1) firstIndex = found;
        // word-boundary bonus: start of string or after a non-alphanumeric separator.
        const atBoundary = found === 0 || /[^a-z0-9]/.test(t[found - 1]);
        if (atBoundary) score += 6;
        // contiguity bonus + span coalescing.
        if (found === prevMatch + 1) {
            score += 8;
            spans[spans.length - 1].end = found + 1;
        } else {
            spans.push({ start: found, end: found + 1 });
        }
        score += 1;                 // base per matched char
        prevMatch = found;
        ti = found + 1;
    }
    score -= firstIndex;            // earlier first match ranks higher
    return { score, spans };
}

/** Rank `items` by `getText` against `query`, keep matches, sort by score desc (stable on
 *  title for ties), cap at `limit` (§D.1 ~30). Empty query → the items in their given order
 *  (no scoring), capped. Returns each surviving item with its match spans. */
export function rankItems<T>(
    query: string, items: readonly T[], getText: (t: T) => string, limit = 30,
): { item: T; spans: MatchSpan[] }[] {
    if (!query.trim()) return items.slice(0, limit).map(item => ({ item, spans: [] }));
    const scored: { item: T; res: FuzzyResult }[] = [];
    for (const item of items) {
        const res = fuzzyMatch(query, getText(item));
        if (res) scored.push({ item, res });
    }
    scored.sort((a, b) => b.res.score - a.res.score || getText(a.item).localeCompare(getText(b.item)));
    return scored.slice(0, limit).map(s => ({ item: s.item, spans: s.res.spans }));
}

/** The payload state the command-set assembly reads (a DOM-free projection of DocPayload). */
export interface PaletteContext {
    /** every live feature, for "Go to feature" (id + title + a detail like "3 refs · file"). */
    features: { id: string; title: string; detail?: string; bound: boolean }[];
    /** feature ids by attention bucket (filtered nav lists, §D.2). */
    driftFids: string[];
    pendingFids: string[];      // features carrying a proposal
    divergentFids: string[];
    /** the currently selected/active feature (for the contextual single-feature actions). */
    activeFid: string | null;
    activeTitle: string;
    activeHeld: boolean;        // the active feature is in the hold set → "Withdraw realization"
    activeBound: boolean;       // the active feature has a binding → "Open bound code"
    /** counts driving the bulk actions. */
    pendingEventCount: number;
    draftCount: number;
    caretInProposal: boolean;   // the caret sits in a proposal → accept/reject at cursor
    glance: boolean;            // current glance pref (for the toggle label)
    featureCount: number;       // 0 → fresh-repo welcome
}

/**
 * Assemble the contextual ACTION items (§D.2). Each carries its C-icon so the palette doubles
 * as the lifecycle legend. Only applicable actions appear (no dead rows). Navigation ("Go to
 * feature") is built separately from `ctx.features` by the view so it can be ranked live.
 */
export function buildActions(ctx: PaletteContext): PaletteItem[] {
    const out: PaletteItem[] = [];
    const push = (id: string, title: string, action: string, icon?: IconName, arg?: string, detail?: string): void => {
        out.push({ id, section: 'actions', title, action, icon, arg, detail });
    };
    if (ctx.pendingEventCount > 0) {
        push('accept-all', `Accept all proposals (${ctx.pendingEventCount})`, 'accept-all', 'check-circle');
        push('reject-all', `Reject all proposals (${ctx.pendingEventCount})`, 'reject-all', 'x-circle');
    }
    if (ctx.caretInProposal) {
        push('accept-cursor', 'Accept change at cursor', 'accept-cursor', 'check-circle');
        push('reject-cursor', 'Reject change at cursor', 'reject-cursor', 'x-circle');
    }
    if (ctx.draftCount > 0) {
        push('hand-off', `Hand to agent (${ctx.draftCount} draft${ctx.draftCount === 1 ? '' : 's'})`, 'hand-off', 'paper-plane-tilt');
    }
    if (ctx.activeFid && ctx.activeHeld) {
        push('withdraw', `Withdraw realization for "${ctx.activeTitle}"`, 'withdraw', 'x-circle', ctx.activeFid);
    }
    if (ctx.activeFid && ctx.activeBound) {
        push('open-code', `Open bound code for "${ctx.activeTitle}"`, 'open-code', 'arrow-bend-down-left', ctx.activeFid);
    }
    push('toggle-glance', ctx.glance ? 'Turn Glance off' : 'Toggle Glance', 'toggle-glance', 'eye');
    // view/misc (always available)
    out.push({ id: 'collapse-all', section: 'view', title: 'Collapse all', action: 'collapse-all' });
    out.push({ id: 'expand-all', section: 'view', title: 'Expand all', action: 'expand-all' });
    return out;
}

/** Build the "Go to feature" nav items (§D.2) — always present, the bulk of the palette. The
 *  bound ones carry ⇧↵ "open code" as a secondary action; the status glyph hints attention. */
export function buildFeatureItems(ctx: PaletteContext): PaletteItem[] {
    const drift = new Set(ctx.driftFids);
    const pending = new Set(ctx.pendingFids);
    const divergent = new Set(ctx.divergentFids);
    return ctx.features.map(f => ({
        id: `goto-${f.id}`,
        section: 'features' as const,
        title: f.title || '(untitled)',
        detail: f.detail,
        icon: (divergent.has(f.id) ? 'warning-diamond'
            : pending.has(f.id) ? 'diamond-fill'
            : drift.has(f.id) ? 'warning-diamond'
            : undefined) as IconName | undefined,
        action: 'goto',
        arg: f.id,
        hasSecondary: f.bound,   // ⇧↵ opens its bound code (the bridge)
    }));
}

/**
 * The empty-query welcome dashboard (§D.3): Recent features (last 3 selected) then the
 * applicable Quick actions ("N proposals to review", "N drafts to hand off"). Useful with zero
 * typing. A fresh repo (no features) returns a single "Run codoc init" affordance.
 */
export function welcomeItems(ctx: PaletteContext, recentFids: readonly string[]): PaletteItem[] {
    if (ctx.featureCount === 0) {
        return [{ id: 'init', section: 'quick', title: 'Run codoc init to bootstrap the tree', action: 'noop' }];
    }
    const out: PaletteItem[] = [];
    const titleOf = new Map(ctx.features.map(f => [f.id, f.title] as const));
    // up to 3 DISTINCT live recents — filter stale + dedupe BEFORE capping so a dead or repeated
    // fid never eats one of the 3 slots (slice-first would).
    const seen = new Set<string>();
    for (const fid of recentFids) {
        if (seen.has(fid) || !titleOf.has(fid)) continue;
        seen.add(fid);
        out.push({ id: `recent-${fid}`, section: 'recent', title: titleOf.get(fid) || '(untitled)', action: 'goto', arg: fid });
        if (seen.size >= 3) break;
    }
    if (ctx.pendingEventCount > 0) {
        out.push({ id: 'quick-review', section: 'quick', title: `${ctx.pendingEventCount} proposal${ctx.pendingEventCount === 1 ? '' : 's'} to review`, action: 'accept-all', icon: 'check-circle' });
    }
    if (ctx.draftCount > 0) {
        out.push({ id: 'quick-handoff', section: 'quick', title: `${ctx.draftCount} draft${ctx.draftCount === 1 ? '' : 's'} to hand off`, action: 'hand-off', icon: 'paper-plane-tilt' });
    }
    return out;
}

/** The single "Create feature" affordance shown on a no-match query (§D.3) — turns a dead end
 *  into authoring. Returns null for an empty query (the welcome shows instead). */
export function createFeatureItem(query: string): PaletteItem | null {
    const t = query.trim();
    if (!t) return null;
    return { id: 'create', section: 'actions', title: `Create feature "${t}"`, action: 'create', arg: t, icon: 'circle-dashed' };
}

/** Human-readable section header (§D.1 uppercase tracked label). */
export function sectionLabel(section: PaletteItem['section']): string {
    switch (section) {
        case 'features': return 'Features';
        case 'actions': return 'Actions';
        case 'recent': return 'Recent';
        case 'quick': return 'Quick actions';
        case 'view': return 'View';
    }
}
