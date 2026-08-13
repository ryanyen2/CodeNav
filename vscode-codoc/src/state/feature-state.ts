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
 *   working  — the agent is in this feature's code right now. The most volatile fact
 *              on screen and the one that explains why anything else is changing.
 *   proposed — a pending agent change is waiting on your verdict. The only state that
 *              blocks on the human, so it outranks everything downstream of it.
 *   sent     — you handed an edit off; the agent will implement it. Nothing to do.
 *   staged   — you edited but have not sent it. Something to do (⌘S), but yours.
 *   planned  — accepted intent with no code behind it yet. A standing condition, not
 *              an event: carried by the italic/dimmed title, no glyph at all.
 *   settled  — nothing to say. Draw nothing.
 *
 * Pure and unit-tested (feature-state.test.ts); the DOM lives in doc-view.ts.
 */
import type { IconName } from '../webview/icons';

export type FeatureState = 'working' | 'proposed' | 'sent' | 'staged' | 'planned' | 'settled';

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
    if (s.sent) return 'sent';
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
        case 'sent':
            return {
                cls: 'sent',
                icon: 'diamond-fill',
                title: 'Sent to the agent — nothing to do. '
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

export type RailState =
    | 'busy' | 'working' | 'proposed' | 'rewritten'
    | 'sent' | 'staged' | 'planned' | 'retired' | 'settled';

export interface RailSignals extends FeatureSignals {
    /** The section is being rewritten right now (translation batch pending /
     *  agent applying) — outranks everything: it explains why the rest moves. */
    busy?: boolean;
    /** An unasked loop rewrite awaits its Keep/Restore verdict. */
    autoEdit?: boolean;
    retired?: boolean;
}

/** First match wins — same discipline as `featureState`, and the two agree on the
 *  shared states so a row badge and its rail tick can never tell different stories. */
export function railState(s: RailSignals): RailState {
    if (s.busy) return 'busy';
    if (s.activeMode) return 'working';
    if (s.proposalOp) return 'proposed';
    if (s.autoEdit) return 'rewritten';
    if (s.sent) return 'sent';
    if (s.staged) return 'staged';
    if (s.realized === false) return 'planned';
    if (s.retired) return 'retired';
    return 'settled';
}

/** One glossary line per rail state — the legend popover + each tick's hover. */
export const RAIL_STATE_LABEL: Record<RailState, string> = {
    busy: 'being rewritten right now (hands off — it updates itself)',
    working: 'the agent is in this feature\'s code',
    proposed: 'an agent change awaits your verdict',
    rewritten: 'codoc rewrote this — review Keep / Restore',
    sent: 'sent to the agent — being realized',
    staged: 'your edit, recorded but not sent (⌘S sends)',
    planned: 'planned — no code behind it yet',
    retired: 'retired',
    settled: 'settled',
};
