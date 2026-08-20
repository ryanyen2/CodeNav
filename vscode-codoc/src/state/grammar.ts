/**
 * grammar.ts — the unified "disagreement grammar" (U3), single source of truth.
 *
 * Every pending change reads the same way: COLOUR encodes *direction* (who is behind),
 * SHAPE encodes *kind* (what changed). Two hues only — code-ahead (you resolve) and
 * doc-ahead (the agent resolves) — never one colour per op-type. Lifecycle (unrealized)
 * and live agent activity are NOT pending changes and live on a separate non-colour
 * status axis (see doc-view.css), so they are deliberately absent here.
 */

/**
 * `yours` is the third direction, and it is not a third hue: a change the reader
 * themself authored, parked for their own review. The daemon produces exactly one —
 * `loop_b._resolve_content`'s DEFERRED outcome, where two peers rewrote the same lines
 * and neither outranks the other, so the author's text is kept as a pending proposal
 * rather than silently dropped or silently applied over someone else's.
 *
 * It needs its own value because the surface had nowhere else to put it: every sidecar
 * proposal was stamped `code-ahead`, so the strip printed "from code" over the reader's
 * own sentence and asked them to accept it from the codebase.
 */
export type Direction = 'code-ahead' | 'doc-ahead' | 'yours';
export type Kind = 'amend' | 'add' | 'move' | 'retire';

/** CSS var for a direction's hue. The hue answers WHO RESOLVES this, which is why
 *  there are still only two: code-ahead and `yours` are both the reader's to settle
 *  (review-blue), doc-ahead waits on the agent (await-green). */
export function directionColorVar(d: Direction): string {
    return d === 'doc-ahead' ? 'var(--dir-await)' : 'var(--dir-review)';
}

/** The resolution actions a direction offers the HUMAN. Code-ahead and `yours` are
 *  both the human's verdict (`[Reject, Accept]` — for `yours`, whether their own
 *  wording replaces the text that beat it to the store); a doc-ahead suggestion is
 *  applied by the AI side (Loop B's intent drain → the agent), so the human's only
 *  verb on their own suggestion is `[Withdraw]`. */
export function directionActions(d: Direction): readonly string[] {
    return d === 'doc-ahead' ? ['Withdraw'] : ['Reject', 'Accept'];
}

/** The words a direction PRINTS — one plain phrase naming whose text the reader is
 *  looking at. It lives here because the decoration layer hard-coded "from code" for
 *  every proposal it drew, including the ones that were the reader's own words. */
export function directionOrigin(d: Direction): string {
    switch (d) {
        case 'code-ahead': return 'from code';
        case 'doc-ahead': return 'your edit';
        case 'yours': return 'your version — waiting for review';
    }
    const _never: never = d;
    return _never;
}

/**
 * The words the origin chip PRINTS, given both axes.
 *
 * `Direction` answers WHO RESOLVES this, and it has three values because that is how
 * many answers the question has. It was also being asked a second question it cannot
 * answer — WHERE THE WORDS CAME FROM — and every machine proposal resolves the same
 * way (`directionFromActor` maps every non-human actor to `code-ahead`), so every
 * machine proposal printed "from code". Including a PLAN, whose entire premise is that
 * the code does NOT say this yet. The node then carried "from code" beside
 * "Accept & build": one chip claiming the work was done, the button beside it asking
 * for the work to be done.
 *
 * The daemon already answers the second question — `render._source_tag` ships "agent
 * plan" / "agent reflection" / "code drift" / "your edit" — so the label reads the tag
 * and the hue keeps reading the direction. A payload with no tag falls back to the old
 * wording, which is what those rows meant.
 */
export function originLabel(d: Direction, tag?: string): string {
    if (d !== 'code-ahead') return directionOrigin(d);
    return (tag ?? '').includes('plan') ? 'from a plan' : directionOrigin(d);
}

/** One sentence for the hover: where these words came from, and what that implies. */
export function originNote(d: Direction, tag?: string): string {
    if (d === 'code-ahead' && (tag ?? '').includes('plan')) {
        return 'An agent proposed this wording before the code exists. '
            + 'Accepting asks for the code to be written.';
    }
    return directionNote(d);
}

/** The non-colour direction marker (REQUIRED for colourblind parity, R8 — the hue is
 *  never the only signal): the glyph plus `directionOrigin`. */
export function directionLabel(d: Direction): string {
    return (d === 'code-ahead' ? '▲ ' : '▼ ') + directionOrigin(d);
}

/** Why this change is sitting here, in one sentence, for the origin chip's hover.
 *  A deferred edit of the reader's own is the case that most needs it: nothing on
 *  screen otherwise explains why words they typed are un-applied. */
export function directionNote(d: Direction): string {
    switch (d) {
        case 'code-ahead': return 'The code changed; the tree may need to follow.';
        case 'doc-ahead': return 'Waiting for the agent to implement this.';
        case 'yours': return 'Someone else changed these same lines while you were editing, '
            + 'so your version was not applied. Nothing of yours was thrown away.';
    }
    const _never: never = d;
    return _never;
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

/** What each button DOES, as its hover text.
 *
 * The code consequence of a `yours` amend and a code-drift amend is identical
 * (`record` — no code changes), and the sentence for them is not: one adopts the
 * codebase's wording, the other re-applies the reader's own over the text that beat it
 * to the store. `consequenceNote` alone said "Updates the tree to match code that
 * already exists" on both, which on a deferred edit of your own is simply not what the
 * click does. */
export function verdictHints(d: Direction, c: Consequence): { accept: string; reject: string } {
    if (d === 'yours') {
        return {
            accept: 'Applies your wording over the version that landed while you were editing.',
            reject: 'Discards your version. The text that landed stays as it is.',
        };
    }
    return {
        accept: consequenceNote(c),
        reject: leavesForAgent(c)
            ? 'Discard this request. Nothing is written.'
            : 'Discard this update. The tree keeps its current wording.',
    };
}
