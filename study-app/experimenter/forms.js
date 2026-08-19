// Everything the researcher types during a session.
//
// It exists so nothing is on paper. The sign-off has to be captured word for
// word while it is being said, the decisions have to be noted while it is still
// obvious who settled them, and the questions have to be scored with the rules
// in front of you rather than from memory an hour later.
import questions from './questions.json' with { type: 'json' };
import afterQuestions from './after-questions.json' with { type: 'json' };

/**
 * The four problems planted in the recorded change, kept short because the list
 * is read while listening to somebody. The rating guide for each is in the
 * project's STUDY.md, which is the answer key.
 */
export const OPEN_DECISIONS = Object.freeze({
    scribe: [
        'The new default loosens the repeated-line rule',
        'The notes were renumbered without being asked',
        'The settings are read after furniture, so the order flipped',
        'The description still promises a prefix keeps its hyphen',
    ],
    tally: [
        'The new setting counts money moved between your own accounts',
        'Weeks line up on the posted date, so the month moved',
        'The weekly view compares rows without the merchant',
        'An unmatched merchant now stops the run',
    ],
});

/**
 * The third problem in each list is the coupled one, where two rules meet.
 *
 * In scribe the furniture rule and the heading rule now run in the opposite
 * order. In tally, leaving out money moved between your own accounts and
 * removing a row recorded twice are the same pair of rows seen differently. Both
 * are where a change that looks local is not, and both leave the tests passing.
 */
export const COUPLED_DECISION = 2;

/**
 * How well each planted problem was found.
 *
 * Attribution is what separates 1 from 2. Somebody who says "this looks wrong"
 * has noticed; somebody who says which commitment it contradicts has understood
 * the change, and only the second is what the description is supposed to buy.
 */
export const CONSISTENCY = Object.freeze([
    '0, not found, or found and waved through',
    '1, found, but not tied to what it contradicts',
    '2, found and tied to the commitment it contradicts',
]);

/**
 * The decoy, and anything else correct they called wrong.
 *
 * A surface that makes everything look suspicious is not an improvement, and
 * without this nothing in the analysis would catch that.
 */
export const FALSE_ALARMS = Object.freeze({
    scribe: ['The character replacements became standard normalisation (the decoy)'],
    tally: ['The rule loop became a prepared ordered mapping (the decoy)'],
});

export const SETTLED_BY = Object.freeze([
    'They found it and directed the fix',
    'The agent proposed it, they accepted deliberately',
    'It stands, and they never noticed',
]);

/**
 * The sign-off is no longer here.
 *
 * It used to be typed into this form while somebody spoke, which made it a
 * record of how well they explained themselves and how fast the researcher could
 * type. The participant answers it on their own page now, straight after the
 * task, and it arrives here read-only along with everything else they wrote.
 */

export const questionsFor = (project) => questions[project] || [];

/** The closed-book set asked after the task, about the change they just made. */
export const afterFor = (project) => afterQuestions[project] || [];

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
// One sitting. The quiz used to be asked again after the task, and the change
// between the two was the measure; both sittings asked about the CODEBASE. What
// comes after the task now is a different set, closed book and in their own
// words, about the change they themselves made — see REFLECTION in the
// instrument. It has no answer key, so it is not scored here.
export const SITTINGS = Object.freeze(['before']);

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
    // How well each planted problem was found, and who settled it. Detection is
    // the primary outcome, so it is stored beside who settled it rather than
    // folded into one number.
    const decisions = {};
    const consistency = {};
    for (const d of OPEN_DECISIONS[project] || []) {
        decisions[d] = null;
        consistency[d] = null;
    }
    return {
        decisions,
        consistency,
        // Correct parts of the change the participant called wrong, including the
        // decoy. Null rather than zero, because "none" and "not asked" are
        // different and only one of them is a result.
        falseAlarms: null,
        falseAlarmNotes: '',
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
    const undecided = Object.entries(a.decisions || {}).filter(([, v]) => !v).length;
    if (undecided) gaps.push(`${undecided} problem${undecided > 1 ? 's' : ''} unattributed`);

    // Detection is the primary outcome, and it is the one thing here that cannot
    // be recovered afterwards, because it depends on having watched them look.
    const unrated = Object.entries(a.consistency || {}).filter(([, v]) => v == null).length;
    if (unrated) gaps.push(`${unrated} problem${unrated > 1 ? 's' : ''} unrated for detection`);

    // A session with no false alarms is a result. A session where nobody wrote
    // the number down is not, and the two look the same a week later.
    if (a.falseAlarms == null) gaps.push('false alarms not recorded');

    // The quiz is not listed. The participant answers it themselves, so a gap
    // there is theirs to close and appears on their own page, not here.
    return gaps;
}

export { questions };
