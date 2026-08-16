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
    scribe: [
        'What marks a quote in extracted text',
        'Whether de-hyphenation applies inside a quote',
        'Whether a quote ends the paragraph before it',
        'What happens to a quote running across a page break',
    ],
    tally: [
        'How a split is written in the CSV',
        'Whether a split counts as one transaction or two',
        'Whether the duplicate rule sees the halves as duplicates',
        'What happens when one half matches no category rule',
    ],
});

/**
 * The last decision in each list is the coupled one, where two rules meet.
 *
 * scribe strips page furniture before it looks for quotes; tally recognises
 * transfers before it drops duplicates. Both are reached by deciding rather than
 * by tripping over them, and both are where somebody can produce working code
 * that contradicts the codebase.
 */
export const COUPLED_DECISION = 3;

/**
 * How consistent each decision is with what the codebase already believes.
 *
 * Consistency rather than correctness. There is no single right answer to any of
 * these; there are answers that fit and answers that contradict, and the rating
 * guide for each one is in the project's STUDY.md.
 */
export const CONSISTENCY = Object.freeze([
    '0 — contradicts what the codebase already does',
    '1 — defensible, but not what this codebase would do',
    '2 — consistent with the existing intent',
]);

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

/** The four bands, in the order RQ1 asks them. */
export const BANDS = Object.freeze(['Purpose', 'Rationale', 'Change', 'Extension']);

/**
 * The whole quiz, asked twice: once before the task and once after.
 *
 * Asking the same twelve both times is the point. Neither answer on its own says
 * much — somebody may know the domain, or may guess well — and the CHANGE
 * between them is what a session did to their understanding. Splitting the
 * questions across the two sittings would make the two scores incomparable.
 */
export const SITTINGS = Object.freeze(['before', 'after']);

export function bandsFor(project) {
    const all = questionsFor(project);
    return BANDS.map((band) => ({ band, questions: all.filter((q) => q.band === band) }))
        .filter((group) => group.questions.length);
}

/** A blank record, so the shape is the same before and after anything is typed. */
export function emptyAssessment(project) {
    const answers = {};
    for (const sitting of SITTINGS) {
        for (const q of questionsFor(project)) {
            // Which letter they picked, stored rather than whether it was right.
            // A stored right-or-wrong cannot be re-marked if a question turns out
            // to be ambiguous, and it hides which wrong answer attracted people.
            answers[`q${q.n}-${sitting}`] = null;
        }
    }
    // Consistency with the codebase's existing intent, per open decision. This
    // is the primary outcome, so it is stored beside who settled it rather than
    // folded into one number.
    const decisions = {};
    const consistency = {};
    for (const d of OPEN_DECISIONS[project] || []) {
        decisions[d] = null;
        consistency[d] = null;
    }
    return {
        signoffConfidence: null,
        signoffGrounds: [],
        signoffVerbatim: '',
        decisions,
        consistency,
        answers,
        updatedAt: null,
    };
}

/** How many of the twelve were right, at one sitting. */
export function score(assessment, project, sitting) {
    const answers = (assessment || {}).answers || {};
    let right = 0;
    let answered = 0;
    for (const q of questionsFor(project)) {
        const given = answers[`q${q.n}-${sitting}`];
        if (given == null) continue;
        answered += 1;
        if (given === q.answer) right += 1;
    }
    return { right, answered, of: questionsFor(project).length };
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

    // Consistency is the primary outcome, and it is the one thing here that
    // cannot be recovered afterwards: it depends on having watched them decide.
    const unrated = Object.entries(a.consistency || {}).filter(([, v]) => v == null).length;
    if (unrated) gaps.push(`${unrated} decision${unrated > 1 ? 's' : ''} unrated for consistency`);

    // The quiz is not listed. The participant answers it themselves, so a gap
    // there is theirs to close and appears on their own page, not here.
    return gaps;
}

export { questions };
