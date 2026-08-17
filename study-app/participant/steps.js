// The order of a session.
//
// The instrument itself is in instrument.js and the project briefings are in
// content.js. This file is only the sequence, and where each set of answers is
// stored so the page and the dashboard agree without either knowing about the
// other.

export {
    CONSENT_FORM, PRESTUDY, REQUIRED, EXCLUDING, shouldExclude,
    AGREE, AMOUNT, CONSTRUCTS, AFTER_CONDITION, scaleFor, keyed,
    MANIPULATION_CHECK, SCENARIOS, DEBRIEF,
} from './instrument.js';
export { PROJECTS, RESPONSIBILITY, HOW_TO_START } from './content.js';
export { QUIZZES } from './quiz.js';

/** The task cards. Drawn as pictures, never as text anyone can copy. */
export const TASK_CARDS = Object.freeze({
    scribe: {
        title: 'Support block quotes',
        lines: [
            'Some of the sample documents quote another document.',
            'Those passages should come out as Markdown block',
            'quotes.',
            '',
            'Decide anything this card does not specify, and be ready',
            'to explain your decisions.',
        ],
    },
    tally: {
        title: 'Support split transactions',
        lines: [
            'One purchase sometimes belongs in two categories: a',
            'supermarket trip that was half groceries and half a',
            'birthday present.',
            '',
            'Let a transaction be split.',
            '',
            'Decide anything this card does not specify, and be ready',
            'to explain your decisions.',
        ],
    },
});

/**
 * The order of the session, built from which way round this participant goes.
 *
 * `order` is 'codoc-first' or 'baseline-first'. The projects alternate with it,
 * so nobody meets the same task twice.
 *
 * Each condition is four steps rather than the old three: how to start it, what
 * the project is, the task, and the questions. Starting used to be read off a
 * script on the call, which meant one participant could get a fuller
 * explanation than the next of the thing being compared.
 */
export function buildSteps(order = 'codoc-first') {
    const codocFirst = order === 'codoc-first';
    const first = { condition: codocFirst ? 'codoc' : 'baseline', project: 'scribe' };
    const second = { condition: codocFirst ? 'baseline' : 'codoc', project: 'tally' };

    // The quiz is asked twice, either side of the task, and it is the same
    // twelve questions both times. Neither answer says much on its own —
    // somebody may know the domain, or guess well — and the CHANGE between them
    // is what the session did to their understanding.
    const forCondition = (c, n) => [
        { id: `intro-${n}`, kind: 'intro', ...c, n },
        { id: `about-${n}`, kind: 'about', ...c, n },
        { id: `quiz-before-${n}`, kind: 'quiz', sitting: 'before', ...c, n },
        { id: `task-${n}`, kind: 'task', ...c, n },
        { id: `quiz-after-${n}`, kind: 'quiz', sitting: 'after', ...c, n },
        { id: `after-${n}`, kind: 'questionnaire', ...c, n },
    ];

    return [
        { id: 'welcome', kind: 'welcome' },
        { id: 'consent', kind: 'consent' },
        { id: 'prestudy', kind: 'prestudy' },
        { id: 'setup', kind: 'setup' },
        ...forCondition(first, 1),
        { id: 'break', kind: 'break' },
        ...forCondition(second, 2),
        { id: 'scenarios', kind: 'scenarios' },
        { id: 'debrief', kind: 'debrief' },
        { id: 'done', kind: 'done' },
    ];
}

/** Where a set of answers gets stored, so the page and the dashboard agree. */
export function answerDoc(step) {
    if (step.kind === 'prestudy') return 'prestudy';
    if (step.kind === 'quiz') return `quiz-${step.project}-${step.sitting}`;
    if (step.kind === 'questionnaire') return `after-${step.condition}`;
    if (step.kind === 'scenarios') return 'scenarios';
    if (step.kind === 'debrief') return 'debrief';
    return null;
}
