// The order of a session.
//
// The instrument itself is in instrument.js and the project briefings are in
// content.js. This file is only the sequence, and where each set of answers is
// stored so the page and the dashboard agree without either knowing about the
// other.

export {
    CONSENT_FORM, PRESTUDY, REQUIRED, EXCLUDING, shouldExclude,
    AGREE, AMOUNT, PERFORMANCE, CONSTRUCTS, AFTER_CONDITION, scaleFor, keyed,
    normalized, rtlx, umuxLite, constructScore,
    SCENARIOS, SIGNOFF, INTERVIEW, INTERVIEW_QUESTIONS,
} from './instrument.js';
export { PROJECTS, TASK, HOW_TO_START } from './content.js';
export { TUTORIAL } from './tutorial.js';

/**
 * The order of the session, built from which way round this participant goes.
 *
 * `order` is 'codoc-first' or 'baseline-first'. The projects alternate with it,
 * so nobody meets the same task twice.
 */
export function buildSteps(order = 'codoc-first') {
    const codocFirst = order === 'codoc-first';
    const first = { condition: codocFirst ? 'codoc' : 'baseline', project: 'scribe' };
    const second = { condition: codocFirst ? 'baseline' : 'codoc', project: 'tally' };

    // Five minutes on the project, five on the way of working, twenty on the
    // task, then the questions. The open-book question round that used to sit
    // before the task is gone: it asked about the CODEBASE, and the task now
    // asks somebody to review a change to it, so the first ten minutes of the
    // task were the question round again with the clock running twice.
    //
    // The closed-book round that used to sit between the sign-off and the
    // questionnaire is gone too, on 2026-08-22. It was five multiple-choice
    // questions with right answers plus one scale asking how much of them the
    // participant had actually known, and the scale went with them: "how much of
    // THAT were you sure of" has no referent once the questions it points at are
    // not asked. What somebody retained is asked out loud in the closing
    // interview instead.
    //
    // The tutorial is its own step because a participant used to meet the way of
    // working in the same minute they met the codebase, with four lines of text
    // to explain it. Both conditions get one, of the same length, so the page
    // does not teach more in one arm than the other.
    const forCondition = (c, n) => [
        { id: `intro-${n}`, kind: 'intro', ...c, n },
        { id: `about-${n}`, kind: 'about', ...c, n },
        { id: `system-${n}`, kind: 'system', ...c, n },
        { id: `task-${n}`, kind: 'task', ...c, n },
        { id: `signoff-${n}`, kind: 'signoff', ...c, n },
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
        { id: 'interview', kind: 'interview' },
        { id: 'done', kind: 'done' },
    ];
}

/** Where a set of answers gets stored, so the page and the dashboard agree. */
export function answerDoc(step) {
    if (step.kind === 'prestudy') return 'prestudy';
    if (step.kind === 'questionnaire') return `after-${step.condition}`;
    if (step.kind === 'scenarios') return 'scenarios';
    if (step.kind === 'signoff') return `signoff-${step.condition}`;
    // The task stores no answers, but it does store its clock. It is the only
    // record of when the task began and ended, and the interaction log is cut on
    // those two instants.
    if (step.kind === 'task') return `task-${step.condition}`;
    // The interview is spoken and typed into the dashboard, so this page stores
    // nothing for it.
    return null;
}
