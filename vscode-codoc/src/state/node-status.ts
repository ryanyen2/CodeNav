/**
 * node-status.ts — the feature's marker, when a feature can be in more than one
 * state at once.
 *
 * `feature-state.ts` collapses everything to ONE state by rank, and for the lifecycle
 * it models that is right: `working`, `proposed`, `sent`, `staged` really are stages
 * of one progression, and showing four at once made the row a legend nobody had.
 *
 * But the three settlement CHANNELS (settlement.ts) are not stages of each other, and
 * a feature genuinely sits in several at once. The case that proves it is the one the
 * old ranking could not say at all: a node that was PLANNED, then BUILT, and what got
 * built is not quite what was planned. Rank picks one of those three facts and drops
 * the other two, leaving the reader to discover the divergence by reading. So this
 * marker ACCUMULATES — but along fixed slots, not as an open-ended pile of chips:
 *
 *     ●        who is behind          human ink: blue
 *     ○        whether it was planned  plan ink: gray
 *     ±        whether it diverged     the diff sign
 *
 * At most three glyphs, each answering a different question, each in the ink its
 * channel already owns in the prose. A settled feature draws none of them, which is
 * still the overwhelming majority of a document and is what keeps the margin quiet.
 *
 * ## Filling means "the code says this now"
 *
 * Both rings fill on the same event and it means the same thing in both: the claim
 * reached the code. An outline is a promise, a fill is a fact — so a hollow blue dot
 * is your edit waiting, and a solid one is your edit built. That is why fulfilment is
 * not a fourth slot: it is the transition every channel already ends in.
 *
 * ## And then it goes away
 *
 * A fulfilled marker is an acknowledgement, not a record — the record is the change
 * ledger. It lingers long enough to be read on the reader's next pass through the
 * document and then leaves, which is what `expire` is for. Nothing else expires on a
 * timer: an open edit and an unbuilt plan are conditions, and a condition that fades
 * out is a surface lying about what is still true.
 *
 * Pure — `now` is a parameter, never read from the clock here, so the drop rules are
 * unit-testable and the History scrubber can ask what the margin said at any moment.
 */
import type { Claim } from './settlement';

/** How far the author's own edits have got. */
export type HumanMark = 'none' | 'open' | 'committed' | 'fulfilled';
/** How far a plan has got. */
export type PlanMark = 'none' | 'proposed' | 'accepted' | 'fulfilled';
/** Whether the built result differs from what was asked for, and in which direction. */
export type DiffMark = 'none' | 'add' | 'del' | 'both';

export interface NodeStatus {
    human: HumanMark;
    plan: PlanMark;
    diff: DiffMark;
}

export const SETTLED: NodeStatus = { human: 'none', plan: 'none', diff: 'none' };

export function isSettled(s: NodeStatus): boolean {
    return s.human === 'none' && s.plan === 'none' && s.diff === 'none';
}

/** A fulfilment the reader has not been shown yet, with the moment it landed. The
 *  claims are gone by then (the text agrees again), so the marker outlives them and
 *  has to be carried separately. */
export interface Fulfilment {
    channel: 'human' | 'plan';
    /** Epoch ms. Supplied by the caller — see the header on why this is not read here. */
    at: number;
    /** The build changed what was asked for, and how. `none` ⇒ it landed as written. */
    diverged: DiffMark;
}

/** How long a fulfilled marker stays in the margin. Long enough to be caught on the
 *  next read-through of the document, short enough that the margin is not a changelog. */
export const FULFILMENT_TTL_MS = 30 * 60 * 1000;

/**
 * The marker for one feature.
 *
 * Read from the claims themselves rather than from a parallel set of booleans, so the
 * marker and the prose can never disagree about whether anything is outstanding — the
 * failure mode of every badge that is computed from its own inputs.
 */
export function nodeStatus(
    claims: readonly Claim[], fulfilment: Fulfilment | null, now: number,
): NodeStatus {
    let human: HumanMark = 'none';
    let plan: PlanMark = 'none';
    let add = false, del = false;

    for (const c of claims) {
        if (c.channel === 'human') {
            // `committed` outranks `open`: a feature with both has already been handed
            // off, and the marker's job is to say whether anything is still yours to send.
            if (c.stage === 'committed' && human === 'none') human = 'committed';
            else if (c.stage === 'open') human = 'open';
        } else if (c.channel === 'plan') {
            if (c.stage === 'accepted' && plan === 'none') plan = 'accepted';
            else if (c.stage === 'proposed') plan = 'proposed';
        } else {
            if (c.edit === 'add') add = true; else del = true;
        }
    }

    if (fulfilment && now - fulfilment.at < FULFILMENT_TTL_MS) {
        if (fulfilment.channel === 'human' && human === 'none') human = 'fulfilled';
        if (fulfilment.channel === 'plan' && plan === 'none') plan = 'fulfilled';
        if (fulfilment.diverged === 'add' || fulfilment.diverged === 'both') add = true;
        if (fulfilment.diverged === 'del' || fulfilment.diverged === 'both') del = true;
    }

    const diff: DiffMark = add && del ? 'both' : add ? 'add' : del ? 'del' : 'none';
    return { human, plan, diff };
}

/** Whether a fulfilment marker is still owed a showing. Callers prune with this rather
 *  than letting the map grow for the life of the workspace. */
export function expire(f: Fulfilment, now: number): boolean {
    return now - f.at >= FULFILMENT_TTL_MS;
}

// ── presentation ─────────────────────────────────────────────────────────────

/** One glyph in the marker: which slot it occupies, the CSS modifier, and whether it
 *  is the one thing on screen allowed to move. */
export interface StatusGlyph {
    slot: 'human' | 'plan' | 'diff';
    cls: string;
    /** `±` / `+` / `−` for the diff slot; the dots are drawn in CSS. */
    text?: string;
    title: string;
}

const DIFF_TEXT: Record<Exclude<DiffMark, 'none'>, string> = { add: '+', del: '−', both: '±' };

const DIFF_TITLE: Record<Exclude<DiffMark, 'none'>, string> = {
    add: 'The build added wording that was not asked for.',
    del: 'The build dropped wording that was asked for.',
    both: 'What was built differs from what was asked for — added and dropped wording.',
};

const HUMAN_TITLE: Record<Exclude<HumanMark, 'none'>, string> = {
    open: 'Your edit, recorded here only. ⌘S sends it to the agent.',
    committed: 'Your edit is with the agent — nothing to do.',
    fulfilled: 'The code now says what you wrote.',
};

const PLAN_TITLE: Record<Exclude<PlanMark, 'none'>, string> = {
    proposed: 'Planned wording, awaiting your verdict. Nothing is built yet.',
    accepted: 'Planned and accepted. No code behind it yet.',
    fulfilled: 'This was planned, and it has been built.',
};

/**
 * The marker as glyphs, in slot order (human, plan, diff) so the row reads left to
 * right the way the sentence does: whose, planned, and whether it drifted. An empty
 * array is the common answer.
 */
export function statusGlyphs(s: NodeStatus): StatusGlyph[] {
    const out: StatusGlyph[] = [];
    if (s.human !== 'none') {
        out.push({ slot: 'human', cls: 'st-human ' + s.human, title: HUMAN_TITLE[s.human] });
    }
    if (s.plan !== 'none') {
        out.push({ slot: 'plan', cls: 'st-plan ' + s.plan, title: PLAN_TITLE[s.plan] });
    }
    if (s.diff !== 'none') {
        out.push({ slot: 'diff', cls: 'st-diff ' + s.diff, text: DIFF_TEXT[s.diff], title: DIFF_TITLE[s.diff] });
    }
    return out;
}

/** One sentence for the whole marker — the hover on the group, and the accessible
 *  name. Built from the same slots so it cannot drift from the glyphs. */
export function statusTitle(s: NodeStatus): string {
    return statusGlyphs(s).map(g => g.title).join(' ');
}
