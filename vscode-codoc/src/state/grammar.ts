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

/** The resolution actions a direction offers the HUMAN. Code-ahead is the
 *  human's verdict (`[Reject, Accept]`); a doc-ahead suggestion is applied by
 *  the AI side (Loop B's intent drain → the agent), so the human's only verb
 *  on their own suggestion is `[Withdraw]`. */
export function directionActions(d: Direction): readonly string[] {
    return d === 'code-ahead' ? ['Reject', 'Accept'] : ['Withdraw'];
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

// ── What accepting will DO (the axis the surface kept hiding) ─────────────────
//
// "Accept" meant two opposite things and said neither. Most proposals reconcile the
// tree to code that already exists: accepting rewrites words and touches nothing
// else. Two do not — a plan placeholder is a build request, and a delete-code retire
// removes source. The user could not tell which they were clicking, and the cost of
// guessing wrong is asymmetric: a wrong `record` costs a sentence, a wrong `build`
// costs a code change they did not ask for.
//
// So it becomes an explicit axis with its own encoding, applied identically on every
// surface: the VERB says the consequence, a plane glyph marks "this leaves for the
// agent" (the vocabulary Commit & send already established), and the accept motion
// launches rather than settles. Three redundant channels for one bit, none of them
// a new colour.

export type Consequence = 'record' | 'build' | 'remove';

/** Read the consequence off the sidecar's `writes_code`, falling back to the origin
 *  tag for a payload written before that field existed (an older daemon against a
 *  newer IDE) — a plan-tagged proposal is a build request by construction. */
export function consequenceOf(
    writesCode: string | null | undefined, tag?: string,
): Consequence {
    if (writesCode === 'build' || writesCode === 'remove') return writesCode;
    if (writesCode == null && (tag ?? '').includes('plan')) return 'build';
    return 'record';
}

/** The primary button's label. The verb IS the warning — a reader who never hovers
 *  anything still cannot mistake a build for a bookkeeping accept. */
export function consequenceVerb(c: Consequence): string {
    switch (c) {
        case 'build': return 'Accept & build';
        case 'remove': return 'Accept & delete code';
        case 'record': return 'Accept';
    }
    const _never: never = c;
    return _never;
}

/** One plain sentence: what happens to the CODE if you accept. Shown in the hover
 *  text everywhere, and inline on the two consequential kinds. */
export function consequenceNote(c: Consequence): string {
    switch (c) {
        case 'build': return 'Asks the agent to write this. Your code will change.';
        case 'remove': return 'Asks the agent to delete the bound code.';
        case 'record': return 'Updates the tree to match code that already exists. No code changes.';
    }
    const _never: never = c;
    return _never;
}

/** Whether this consequence hands work to the agent — the bit that drives the plane
 *  glyph and the launch motion. */
export function leavesForAgent(c: Consequence): boolean {
    return c !== 'record';
}
