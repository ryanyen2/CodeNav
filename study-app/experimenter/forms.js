// Everything the researcher types during a session.
//
// It exists so nothing is on paper. The sign-off has to be captured word for
// word while it is being said, the decisions have to be noted while it is still
// obvious who settled them, and the questions have to be scored with the rules
// in front of you rather than from memory an hour later.
import questions from './questions.json' with { type: 'json' };

/**
 * What each task deliberately leaves open, and the three ways one can be
 * settled. Kept short because it is read while listening to somebody.
 */
export const OPEN_DECISIONS = Object.freeze({
    hearth: [
        'What marks a post as a draft',
        'Whether drafts stay out of the feed and the sitemap',
        'How the preview differs from a real build',
        'Whether a draft is not built at all, or built but unlinked',
    ],
    ember: [
        'Where a mute is configured',
        'Whether muted items still reach the notification log and the status counts',
        'Whether an all-muted day still gets a page',
        'Whether the latest page follows the same rule as a dated one',
    ],
});

export const SETTLED_BY = Object.freeze([
    'They decided, before the agent acted',
    'The agent proposed it, they accepted',
    'The agent did it, they never noticed',
]);

/** What the sign-off answer rested on. The number matters less than this. */
export const GROUNDS = Object.freeze([
    'Ran the tests',
    'Read the diff',
    'Read the description',
    'The agent said so',
]);

export const questionsFor = (project) => questions[project] || [];

/**
 * Which questions are asked in which round, with the two anchors appearing in
 * both. Asking them twice is the point: the change between the answers is the
 * measure, not either answer on its own.
 */
export function rounds(project) {
    const all = questionsFor(project);
    return {
        1: all.filter((q) => q.round === 1),
        2: [...all.filter((q) => q.round === 1 && q.repeated), ...all.filter((q) => q.round === 2)],
    };
}

/** A blank record, so the shape is the same before and after anything is typed. */
export function emptyAssessment(project) {
    const scores = {};
    for (const round of [1, 2]) {
        for (const q of rounds(project)[round]) {
            // Closed book and open book are stored separately. The difference
            // between them is the finding, so one must never overwrite the other.
            scores[`${q.code}-r${round}-closed`] = null;
            scores[`${q.code}-r${round}-open`] = null;
            scores[`${q.code}-r${round}-confidence`] = null;
            scores[`${q.code}-r${round}-notes`] = '';
        }
    }
    const decisions = {};
    for (const d of OPEN_DECISIONS[project] || []) decisions[d] = null;
    return {
        signoffConfidence: null,
        signoffGrounds: [],
        signoffVerbatim: '',
        decisions,
        scores,
        updatedAt: null,
    };
}

/** What is still missing, so the gaps are visible before the call ends. */
export function outstanding(assessment, project) {
    const a = assessment || {};
    const gaps = [];
    if (a.signoffConfidence == null) gaps.push('the sign-off number');
    if (!(a.signoffGrounds || []).length) gaps.push('what the sign-off rested on');
    if (!(a.signoffVerbatim || '').trim()) gaps.push('the sign-off in their words');

    const undecided = Object.entries(a.decisions || {}).filter(([, v]) => !v).length;
    if (undecided) gaps.push(`${undecided} open decision${undecided > 1 ? 's' : ''} unattributed`);

    const scores = a.scores || {};
    const missing = Object.keys(emptyAssessment(project).scores)
        .filter((k) => k.endsWith('-closed') && scores[k] == null).length;
    if (missing) gaps.push(`${missing} question${missing > 1 ? 's' : ''} unscored`);
    return gaps;
}

export { questions };
