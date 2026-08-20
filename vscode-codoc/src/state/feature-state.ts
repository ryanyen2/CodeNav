/**
 * feature-state.ts — the ONE state a feature is in, and the one glyph that says so.
 *
 * A feature could previously wear six markers at once: an activity dot, a captured
 * circle, a queued diamond, a divergence warning, an unrealized ring, and an
 * amend/retire chip — each true, each drawn, none ranked. The row became a legend
 * nobody had. The states are not independent facts a reader needs to hold
 * simultaneously; they are stages of one lifecycle, and only the furthest-along one
 * changes what the reader should do next. So this collapses them to a single ordered
 * projection: one state, one glyph, one sentence of hover text saying what it means
 * and what to do about it.
 *
 * ORDER (first match wins) and why:
 *   working   — the agent is in this feature's code right now. The most volatile fact
 *               on screen and the one that explains why anything else is changing.
 *   proposed  — a pending agent change is waiting on your verdict. The only state that
 *               blocks on the human, so it outranks everything downstream of it.
 *   rewritten — the loop rewrote this on its own authority; Keep / Restore is owed.
 *   agreed    — you accepted an agent's PLAN and no code is behind it yet.
 *   sent      — you handed YOUR edit off; the agent will implement it. Nothing to do.
 *   staged    — you edited but have not sent it. Something to do (⌘S), but yours.
 *   planned   — an unrealized placeholder nothing is queued for. A standing condition,
 *               not an event: carried by the italic/dimmed title, no glyph at all.
 *   settled   — nothing to say. Draw nothing.
 *
 * ## The badge is a CHANNEL, and the channel decides the colour
 *
 * `state/settlement.ts` sorts every unsettled thing into three channels — the author's
 * (blue ink), a plan's (gray), the codebase's (green/red ground) — and this row reports
 * the same facts in the margin of a different pane. It used to report them in a palette
 * of its own: proposed in review-blue (the author's colour), sent in sage (the
 * codebase's), staged in the author's blue. Two of those three named the wrong party.
 *
 * So each state here belongs to a channel and takes that channel's ink, and the two
 * states that were one — `sent` and `agreed` — are split because they differ in exactly
 * the thing the colour is reporting: whose words the queue is holding. Same lifecycle
 * position; not the same fact.
 *
 * Pure and unit-tested (feature-state.test.ts); the DOM lives in doc-view.ts.
 */
import type { IconName } from '../webview/icons';

export type FeatureState =
    | 'working' | 'proposed' | 'rewritten' | 'agreed' | 'sent' | 'staged'
    | 'planned' | 'settled';

/** Which settlement channel a state belongs to — the ONE thing that decides its ink.
 *  `null` = not a claim at all (an agent working right now is a fact about the present,
 *  not about who is ahead), so it spends no channel's hue and says itself in motion. */
export const STATE_CHANNEL: Record<FeatureState, 'human' | 'plan' | 'code' | null> = {
    working: null,
    proposed: 'plan',
    rewritten: 'code',
    agreed: 'plan',
    sent: 'human',
    staged: 'human',
    planned: 'plan',
    settled: null,
};

export interface FeatureSignals {
    /** The agent is touching this feature's bound code right now. */
    activeMode?: 'write' | 'read' | null;
    /** A pending agent proposal on this feature (any op). */
    proposalOp?: 'amend' | 'retire' | 'add' | 'move' | null;
    /** The proposal arrived from realizing a DIFFERENT edit of yours — worth saying,
     *  but it is still just "proposed"; it does not earn its own glyph. */
    divergent?: boolean;
    /** Handed off: the agent will implement it. */
    sent?: boolean;
    /** WHOSE words the queue is holding, when `sent` (sidecar `hold_detail[fid].origin`;
     *  `codoc/loop/edits.Directive.origin`). `'plan'` = you accepted an agent's plan;
     *  anything else = your own committed edit. It is the only thing separating the two,
     *  because both are "applied to the store, waiting on the code" and the proposal row
     *  that would have said "an agent wrote this" is deleted by the accept. */
    holdOrigin?: 'human' | 'plan';
    /** The loop rewrote this description on its own authority and a Keep / Restore
     *  verdict is owed. The rail has always shown it; the row did not, so the two panes
     *  disagreed about a feature the codebase had just changed under the reader. */
    autoEdit?: boolean;
    /** Recorded locally, not yet handed off. */
    staged?: boolean;
    /** False ⇒ accepted intent with no code behind it yet. */
    realized?: boolean;
    /** The plain-language gloss of WHAT is queued for this feature (sidecar
     *  `hold_detail[fid].intent`). A feature can hold a queued edit while already
     *  having code — the queue asks for a CHANGE to it — and a badge that only says
     *  "sent" leaves that reading as a contradiction: it has code, so why is it
     *  waiting? Naming the queued work answers it in the same hover. */
    queuedIntent?: string;
}

export function featureState(s: FeatureSignals): FeatureState {
    if (s.activeMode) return 'working';
    if (s.proposalOp) return 'proposed';
    if (s.autoEdit) return 'rewritten';
    if (s.sent) return s.holdOrigin === 'plan' ? 'agreed' : 'sent';
    if (s.staged) return 'staged';
    if (s.realized === false) return 'planned';
    return 'settled';
}

export interface StateBadge {
    /** CSS modifier on the single badge slot. */
    cls: string;
    /** The lifecycle glyph, or null for states drawn without one (a CSS dot / nothing). */
    icon: IconName | null;
    /** One sentence: what this means AND what to do about it. */
    title: string;
}

/** The badge for a state, or null when the state is carried by the row itself
 *  (planned = dimmed italic title; settled = nothing). */
export function stateBadge(state: FeatureState, s: FeatureSignals = {}): StateBadge | null {
    switch (state) {
        case 'working':
            return {
                cls: s.activeMode === 'write' ? 'working write' : 'working read',
                icon: null,   // a pulsing dot, drawn in CSS — motion carries "now"
                title: s.activeMode === 'write'
                    ? 'The agent is changing this feature\'s code right now.'
                    : 'The agent is reading this feature\'s code right now.',
            };
        case 'proposed':
            return {
                cls: 'proposed',
                icon: 'diamond',
                title: (s.divergent
                    ? 'Review this: the agent changed it while implementing another of your edits. '
                    : 'The agent proposes a change here. ')
                    + 'Hover the feature to accept or reject it.',
            };
        case 'rewritten':
            return {
                cls: 'rewritten',
                icon: 'diamond',
                title: 'codoc rewrote this to match code that already changed. '
                    + 'Keep it, or restore your wording, in the document.',
            };
        case 'agreed':
            return {
                cls: 'agreed',
                icon: 'diamond-fill',
                title: 'The plan you accepted is queued — nothing to do. '
                    + (s.queuedIntent
                        ? `The agent will ${s.queuedIntent}.`
                        : 'No code is behind it yet.'),
            };
        case 'sent':
            return {
                cls: 'sent',
                icon: 'diamond-fill',
                title: 'Your edit is with the agent — nothing to do. '
                    + (s.queuedIntent
                        ? `It will ${s.queuedIntent}.`
                        : 'It will change the code to match.'),
            };
        case 'staged':
            return {
                cls: 'staged',
                icon: 'circle-dashed',
                title: (s.queuedIntent
                    ? `Saved here only — the agent will ${s.queuedIntent} once you send it. `
                    : 'Saved here only. ')
                    + 'Press ⌘S to send it to the agent.',
            };
        case 'planned':
        case 'settled':
            return null;
    }
    const _never: never = state;
    return _never;
}

/** Hover text for a planned (accepted, not yet built) feature — it has no glyph, so
 *  the explanation rides on the title itself. */
export const PLANNED_TITLE = 'Planned. No code has been written for this yet.';

// ── the minimap rail's per-tick state ─────────────────────────────────────────
//
// The rail (the strip of ticks on the doc's right edge) shows the WHOLE document's
// status at a glance, one tick per feature, in the same encoding the rows already
// use — so it is the same ordered projection as `featureState`, extended with the
// two facts the rail must show that the row badge does not rank: a section being
// rewritten under the reader right now (busy — translating / the agent applying),
// and an unresolved loop rewrite awaiting its Keep/Restore verdict (rewritten).

export type RailState = FeatureState | 'busy' | 'retired';

export interface RailSignals extends FeatureSignals {
    /** The section is being rewritten right now (translation batch pending /
     *  agent applying) — outranks everything: it explains why the rest moves. */
    busy?: boolean;
    retired?: boolean;
}

/**
 * First match wins — and the ordering is `featureState`'s, extended at both ends
 * rather than restated.
 *
 * It used to be a parallel copy, and the copy had drifted: the rail knew about a loop
 * rewrite and the row did not, so a feature the codebase had just changed under the
 * reader was marked in one pane and silent in the other. Delegating means they cannot
 * drift again — `rewritten` and `agreed` reached the row by being added once, here's
 * two extra states being the only thing this function still decides.
 */
export function railState(s: RailSignals): RailState {
    if (s.busy) return 'busy';
    const state = featureState(s);
    if (state === 'settled' && s.retired) return 'retired';
    return state;
}

/** One glossary line per rail state — the legend popover + each tick's hover. */
export const RAIL_STATE_LABEL: Record<RailState, string> = {
    busy: 'being rewritten right now (hands off — it updates itself)',
    working: 'the agent is in this feature\'s code',
    proposed: 'an agent change awaits your verdict',
    rewritten: 'codoc rewrote this — review Keep / Restore',
    agreed: 'a plan you accepted — no code behind it yet',
    sent: 'your edit, sent to the agent — being realized',
    staged: 'your edit, recorded but not sent (⌘S sends)',
    planned: 'planned — no code behind it yet',
    retired: 'retired',
    settled: 'settled',
};
