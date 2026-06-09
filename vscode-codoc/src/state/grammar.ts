/**
 * grammar.ts — the unified "disagreement grammar" (U3), single source of truth.
 *
 * Every pending change reads the same way: COLOUR encodes *direction* (who is behind),
 * SHAPE encodes *kind* (what changed). Two hues only — code-ahead (you resolve) and
 * doc-ahead (the agent resolves) — never one colour per op-type. Lifecycle (unrealized)
 * and live agent activity are NOT pending changes and live on a separate non-colour
 * status axis (see doc-view.css), so they are deliberately absent here.
 */

export type Direction = 'code-ahead' | 'doc-ahead';
export type Kind = 'amend' | 'add' | 'move' | 'retire';

/** CSS var for a direction's hue. code-ahead = review-blue; doc-ahead = await-green. */
export function directionColorVar(d: Direction): string {
    return d === 'code-ahead' ? 'var(--dir-review)' : 'var(--dir-await)';
}

/** The resolution action pair for a direction, as `[secondary, primary]`. */
export function directionActions(d: Direction): readonly [string, string] {
    return d === 'code-ahead' ? ['Reject', 'Accept'] : ['Withdraw', 'Apply'];
}

/** The non-colour direction marker (REQUIRED for colourblind parity, R8 — the hue is
 *  never the only signal). */
export function directionLabel(d: Direction): string {
    return d === 'code-ahead' ? '▲ from code' : '▼ your edit';
}

/** The lead glyph encoding a kind (a SHAPE, never a colour). Total over `Kind`. */
export function kindGlyph(kind: Kind): string {
    switch (kind) {
        case 'add': return '+';
        case 'move': return '→';
        case 'retire': return '~';
        case 'amend': return '✎';
    }
    // Exhaustiveness guard — a newly-added Kind must be handled above or this won't compile.
    const _never: never = kind;
    return _never;
}
