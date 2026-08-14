// What the participant is asked, and in what order.
//
// Kept apart from the page so it can be checked without a browser. The wording
// here is the study instrument: the questionnaire items come from the design
// document and their order and phrasing are fixed at pre-registration, so treat
// changes to this file as changes to the study rather than as copy edits.

/** The consent form. Identifying answers stay in Google's account, never here. */
export const CONSENT_FORM =
    'https://docs.google.com/forms/d/e/1FAIpQLSdbEPenMgTTf2Wj1xl_iGwvSleN4na9TwuGY8CBgzBCx5LPIw/viewform?embedded=true';

/** Asked once, before the session. */
export const BACKGROUND = Object.freeze([
    { id: 'years', label: 'Years you have been writing software professionally',
      type: 'number', min: 0, max: 60 },
    { id: 'agentUse', label: 'How often do you use a coding agent',
      type: 'choice', options: ['Daily', 'A few times a week', 'Weekly', 'Less often', 'Never'] },
    { id: 'agentTools', label: 'Which ones', type: 'text', placeholder: 'Claude Code, Cursor, Copilot…' },
    // The screening question. "Never" excludes, and the design doc says so, but
    // the page does not tell them that or the answer stops being honest.
    { id: 'readsDiff', label: 'When an agent proposes a change across several files, how often do you read the diff before accepting',
      type: 'choice', options: ['Always', 'Usually', 'About half the time', 'Rarely', 'Never'] },
    { id: 'python', label: 'How comfortable are you reading Python',
      type: 'choice', options: ['Very', 'Fairly', 'A little', 'Not at all'] },
]);

/** Answers that mean this person should not be run. */
export const EXCLUDING = Object.freeze({ readsDiff: ['Never'] });

export function shouldExclude(answers) {
    return Object.entries(EXCLUDING)
        .some(([id, bad]) => bad.includes((answers || {})[id]));
}

/**
 * The twelve items, asked after each condition.
 *
 * Four are reverse keyed. Their answers are stored exactly as given and flipped
 * once during analysis: flipping here would mean the stored data no longer
 * matches what the person saw, and nobody could check it afterwards.
 */
export const AFTER_CONDITION = Object.freeze([
    { id: 'q1', text: 'I always knew what the agent had changed and why.' },
    { id: 'q2', text: 'I could steer the work toward what I wanted with little effort.' },
    { id: 'q3', text: 'Keeping the written description current felt like busywork.', reverse: true },
    { id: 'q4', text: 'Whenever I checked, the written description matched the code.' },
    { id: 'q5', text: 'I accepted changes I had not really reviewed.', reverse: true },
    { id: 'q6', text: 'When I needed to know why something was built a certain way, I could find out quickly.' },
    { id: 'q7', text: 'I lost track of the overall state of the codebase while the agent worked.', reverse: true },
    { id: 'q8', text: 'The effort I spent writing things down paid off within this session.' },
    { id: 'q9', text: 'I would have finished faster without maintaining the written description.', reverse: true },
    { id: 'q10', text: 'If I came back in a month, what is written down would get me back up to speed.' },
    { id: 'q11', text: 'The agent made decisions that were mine to make.', reverse: true },
    { id: 'q12', text: 'I could tell which parts of the result I still needed to check.' },
]);

export const SCALE = Object.freeze({
    min: 1, max: 7,
    lowLabel: 'Strongly disagree',
    highLabel: 'Strongly agree',
});

/** Asked in both conditions, to check the manipulation landed. */
export const MANIPULATION_CHECK = Object.freeze([
    { id: 'noticedChange', label: 'Did you notice the written description changing while the agent worked',
      type: 'choice', options: ['Yes, often', 'Once or twice', 'No'] },
]);

/** Asked once at the very end, with both conditions done. */
export const SCENARIOS = Object.freeze([
    { id: 's1', text: 'Fixing a typo in a repository you have never seen' },
    { id: 's2', text: 'A feature across several modules, in a codebase you will own for a year' },
    { id: 's3', text: 'A throwaway script you will delete tomorrow' },
    { id: 's4', text: 'Getting a new teammate up to speed on this codebase' },
    { id: 's5', text: 'A production hotfix, under time pressure' },
]);

/** The task cards. Shown as pictures, never as text anyone can copy. */
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
        { id: 'background', kind: 'background' },
        { id: 'setup', kind: 'setup' },
        ...forCondition(first, 1),
        { id: 'break', kind: 'break' },
        ...forCondition(second, 2),
        { id: 'scenarios', kind: 'scenarios' },
        { id: 'done', kind: 'done' },
    ];
}

/** Where a set of answers gets stored, so the page and the dashboard agree. */
export function answerDoc(step) {
    if (step.kind === 'background') return 'background';
    if (step.kind === 'questionnaire') return `after-${step.condition}`;
    if (step.kind === 'scenarios') return 'scenarios';
    return null;
}
