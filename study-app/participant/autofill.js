// Filling a step in with defaults, so a pilot can jump to the part being tested.
//
// A pilot exists to find out that something is broken. Answering twenty-five
// scales honestly to reach the step you wanted to look at means the step you
// wanted to look at gets tested once per sitting, which is how a defect survives
// three pilots.
//
// Two rules hold this to pilots and keep it out of the results.
//
// It is offered only to a code that begins `pilot-`, which is decided from the
// code itself rather than from a flag in a document, so a participant cannot
// reach it and a page that never loaded the participant document still knows.
//
// Everything it writes carries `autofilled: true` in the same document as the
// answers. Pilots are already out of the figures by default, so this is the
// second line rather than the first, but it is the one that survives the day
// somebody exports a cohort with pilots included: the marker travels with the
// data instead of living in the dashboard's own idea of who was a pilot.
//
// Defaults come from the instrument itself. A second list of answers here would
// silently stop covering a question the moment one was added.
import {
    PRESTUDY, REQUIRED, EXCLUDING, AFTER_CONDITION, MANIPULATION_CHECK,
    SCENARIOS, SIGNOFF, REFLECTION, scaleFor,
} from './instrument.js';
import { QUIZZES } from './quiz.js';

const SAID = 'Filled in automatically. This is a pilot run.';

/** The middle of a scale, rounded up, so nothing lands on an endpoint. */
const middle = (s) => Math.ceil((s.min + s.max) / 2);

/**
 * One answer for one question, from its own definition.
 *
 * A choice takes the first option that does not exclude the participant. The
 * screening question's excluding answer is a real option, and a pilot filled
 * with it would be dropped from its own pilot run.
 */
function answer(q) {
    const barred = EXCLUDING[q.id] || [];
    switch (q.type) {
        case 'choice':
            return (q.options || []).find((o) => !barred.includes(o)) ?? null;
        case 'multi':
            return (q.options || []).slice(0, 1);
        case 'scale5':
            return 3;
        case 'number':
            return q.min === undefined ? 1 : q.min + 1;
        case 'longtext':
        case 'text':
            return SAID;
        default:
            return SAID;
    }
}

/**
 * Everything a step needs to count as answered, or null if it needs nothing.
 *
 * The shape matches what `complete()` checks, question for question, because a
 * default that does not satisfy the check leaves the button disabled and the
 * skip does nothing visible.
 */
export function defaultsFor(step, project) {
    const out = {};
    switch (step.kind) {
        case 'prestudy':
            // Every required one. The optional ones are left alone: a pilot
            // should see the same page a participant sees, including which
            // questions it lets them past.
            for (const q of PRESTUDY) {
                if (REQUIRED.includes(q.id)) out[q.id] = answer(q);
            }
            break;
        case 'quiz':
            for (const q of QUIZZES[project || step.project] || []) {
                out[`q${q.n}`] = 'a';
            }
            break;
        case 'questionnaire':
            for (const q of AFTER_CONDITION) out[q.id] = middle(scaleFor(q));
            for (const q of MANIPULATION_CHECK) out[q.id] = answer(q);
            break;
        case 'signoff':
            for (const q of SIGNOFF) out[q.id] = answer(q);
            break;
        case 'reflect':
            for (const q of REFLECTION) out[q.id] = answer(q);
            break;
        case 'scenarios':
            for (const s of SCENARIOS) out[s.id] = 'No preference';
            break;
        default:
            return null;    // nothing to answer; the step is read and moved past
    }
    out.autofilled = true;
    return out;
}
