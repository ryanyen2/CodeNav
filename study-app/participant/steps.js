// The order of a session.
//
// The instrument itself is in instrument.js and the project briefings are in
// content.js. This file is only the sequence, and where each set of answers is
// stored so the page and the dashboard agree without either knowing about the
// other.

export {
    CONSENT_FORM, PRESTUDY_FORM, SCREENING, EXCLUDING, shouldExclude,
    AGREE, AMOUNT, CONSTRUCTS, AFTER_CONDITION, scaleFor, keyed,
    MANIPULATION_CHECK, SCENARIOS, DEBRIEF,
} from './instrument.js';
export { PROJECTS, RESPONSIBILITY, HOW_TO_START } from './content.js';

/** The task cards. Drawn as pictures, never as text anyone can copy. */
export const TASK_CARDS = Object.freeze({
    hearth: {
        title: 'Add draft support to hearth',
        lines: [
            'A post marked as a draft must not appear anywhere in a',
            'production build.',
            '',
            'When someone runs the dev server, drafts must appear so',
            'they can be previewed.',
            '',
            'Decide anything this card does not specify, and be ready',
            'to explain your decisions.',
        ],
    },
    ember: {
        title: 'Add mute support to ember',
        lines: [
            'Items from a muted feed must not appear in the daily',
            'digest.',
            '',
            'They must still be fetched, stored, and visible in the',
            'archive and the search file.',
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
    const first = { condition: codocFirst ? 'codoc' : 'baseline', project: 'hearth' };
    const second = { condition: codocFirst ? 'baseline' : 'codoc', project: 'ember' };

    const forCondition = (c, n) => [
        { id: `intro-${n}`, kind: 'intro', ...c, n },
        { id: `about-${n}`, kind: 'about', ...c, n },
        { id: `task-${n}`, kind: 'task', ...c, n },
        { id: `after-${n}`, kind: 'questionnaire', ...c, n },
    ];

    return [
        { id: 'welcome', kind: 'welcome' },
        { id: 'consent', kind: 'consent' },
        { id: 'prestudy', kind: 'prestudy' },
        { id: 'screening', kind: 'screening' },
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
    if (step.kind === 'screening') return 'screening';
    if (step.kind === 'questionnaire') return `after-${step.condition}`;
    if (step.kind === 'scenarios') return 'scenarios';
    if (step.kind === 'debrief') return 'debrief';
    return null;
}
