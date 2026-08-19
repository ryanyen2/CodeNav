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
    MANIPULATION_CHECK, SCENARIOS, SIGNOFF, REFLECTION,
    INTERVIEW, INTERVIEW_QUESTIONS,
} from './instrument.js';
export { PROJECTS, RESPONSIBILITY, HOW_TO_START } from './content.js';
export { QUIZZES, AFTER_QUIZZES } from './quiz.js';

/**
 * The task cards. Drawn as pictures, never as text anyone can copy.
 *
 * Since the 2026-08-19 redesign the task is to review a change an agent already
 * made, so the card restates the request the participant is told they left, and
 * then asks for a decision. It does not say that anything is wrong. A card that
 * said so would prime the measure, and a card that said nothing at all would let
 * a participant ship without looking, which is itself one of the outcomes.
 *
 * The card is the REQUEST and the decision, and nothing else. What happened while
 * they were away, what arrives in the terminal and how long it all takes are on
 * the page around it. A card carrying that too was a picture of the task and of
 * the session's stage directions read as one thing, and a participant meeting it
 * cold could not tell which part was the job.
 */
export const TASK_CARDS = Object.freeze({
    scribe: {
        title: 'Review what the agent did',
        lines: [
            'Before you left you asked for:',
            '',
            'a config file,',
            'a short report next to the output,',
            'and a tidy-up of how the rules get their settings.',
            '',
            'Decide what to keep, and ship it.',
        ],
        // What finished looks like, stated as the end state rather than as a
        // list of things to check. Naming what to check would hand over the
        // detection that is being measured.
        example: {
            label: 'Finished means',
            lines: ['The code does what you meant, and',
                'the description says what the code does.'],
        },
    },
    tally: {
        title: 'Review what the agent did',
        lines: [
            'Before you left you asked for:',
            '',
            'the merchant rules moved into a file you can edit,',
            'a weekly view beside the monthly one,',
            'and a tidy-up of how the rules get their settings.',
            '',
            'Decide what to keep, and ship it.',
        ],
        example: {
            label: 'Finished means',
            lines: ['The code does what you meant, and',
                'the description says what the code does.'],
        },
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

    // The quiz is asked ONCE, before the task, open book and timed: how fast
    // somebody can find twelve answers about a codebase they met today is the
    // thing the two ways of working differ on.
    //
    // Afterwards comes a different question set, closed book, about the change
    // they just made. It used to be the same twelve again, and the change between
    // the two sittings was the measure. But both sittings asked about the
    // CODEBASE, and what the study is about is whether the person still owns
    // their own change. Their change is different every time, so it is asked in
    // their own words and rated by hand.
    const forCondition = (c, n) => [
        { id: `intro-${n}`, kind: 'intro', ...c, n },
        { id: `about-${n}`, kind: 'about', ...c, n },
        { id: `quiz-before-${n}`, kind: 'quiz', sitting: 'before', ...c, n },
        { id: `task-${n}`, kind: 'task', ...c, n },
        { id: `signoff-${n}`, kind: 'signoff', ...c, n },
        { id: `reflect-${n}`, kind: 'reflect', ...c, n },
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
    if (step.kind === 'quiz') return `quiz-${step.project}-${step.sitting}`;
    if (step.kind === 'questionnaire') return `after-${step.condition}`;
    if (step.kind === 'scenarios') return 'scenarios';
    if (step.kind === 'signoff') return `signoff-${step.condition}`;
    // The task stores no answers, but it does store its clock. Without this the
    // interaction log cannot be cut into the two stages the analysis compares:
    // the stretch spent working the codebase OUT (the questions) and the stretch
    // spent changing it (the task). Both are the same actions in the same files,
    // and only the wall clock separates them.
    if (step.kind === 'task') return `task-${step.condition}`;
    if (step.kind === 'reflect') return `reflect-${step.condition}`;
    // The interview is spoken and typed into the dashboard, so this page stores
    // nothing for it.
    return null;
}
