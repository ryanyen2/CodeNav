// Everything the participant is asked, and why each thing is asked.
//
// This file is the study instrument. Wording and order are fixed at
// pre-registration, so a change here is a change to the study rather than a copy
// edit. Every rating item carries the construct it belongs to and the research
// question it answers; an item that can name neither does not belong here, and
// several were cut on exactly that test.
//
// Two of the blocks are published instruments reproduced verbatim (UMUX-Lite,
// NASA-TLX). They are not improved, shortened, or reworded. Their value is that
// a reviewer already knows what the numbers mean and can compare them to other
// papers, and an edited instrument forfeits that.

// ── the forms that live in Google ────────────────────────────────────────────

/** Consent. Identifying answers stay in Google's account and never reach us. */
export const CONSENT_FORM =
    'https://docs.google.com/forms/d/e/1FAIpQLSdbEPenMgTTf2Wj1xl_iGwvSleN4na9TwuGY8CBgzBCx5LPIw/viewform?embedded=true';

/**
 * Demographics and experience, asked once before the session.
 *
 * On this page rather than in a Google form. It used to be a form, which meant
 * the page had to say "this one is here rather than in the form above because
 * the researcher needs to see it" about the one question that could not go
 * there — a sentence that told a participant about our plumbing. Asking
 * everything in one place removes the sentence and the reason for it.
 *
 * Gender and age are asked because a paper has to describe who took part. They
 * are the only answers here that could identify anybody, so both offer a way to
 * decline, and neither is required.
 */
export const PRESTUDY = Object.freeze([
    { id: 'gender', type: 'choice', label: 'Gender',
      options: ['Woman', 'Man', 'Non-binary', 'Prefer to self-describe', 'Prefer not to say'] },
    { id: 'genderSelf', type: 'text', label: 'If you would rather describe it yourself',
      placeholder: 'Optional', showWhen: { gender: 'Prefer to self-describe' } },
    { id: 'age', type: 'number', label: 'Age', min: 18, max: 99, optional: true },
    { id: 'education', type: 'choice', label: 'Highest level of education finished',
      options: ["Bachelor's", "Master's", 'Doctorate', 'Professional degree',
                'Trade or vocational', 'Other'] },
    { id: 'years', type: 'number', label: 'Years you have been programming', min: 0, max: 60 },
    { id: 'aiUse', type: 'choice', label: 'How often do you use AI tools to write code',
      options: ['Almost every day', 'Several times a week', 'About once a week',
                'A few times a month', 'Less than once a month', 'Never'] },
    { id: 'aiFamiliar', type: 'scale5', label: 'How familiar are you with AI coding tools',
      low: 'Not at all', high: 'Very' },
    { id: 'python', type: 'scale5', label: 'How confident are you reading Python',
      low: 'Not at all', high: 'Very' },
    { id: 'aiToUnderstand', type: 'longtext',
      label: 'Have you used an AI tool to understand a codebase you did not write? What happened?',
      placeholder: 'A sentence or two, or leave it blank.', optional: true },
    // The screening question. It sits with the rest now, in the order somebody
    // would naturally answer them, rather than alone on a page that had to
    // explain itself. The page still does not say which answer excludes, or the
    // answer stops being honest.
    { id: 'readsDiff', type: 'choice',
      label: 'When an AI proposes a change across several files, how often do you read the diff before accepting',
      options: ['Always', 'Usually', 'About half the time', 'Rarely', 'Never'] },
]);

/** Which of them must be answered before the page will move on. */
export const REQUIRED = Object.freeze(
    PRESTUDY.filter((q) => !q.optional && !q.showWhen).map((q) => q.id));

/**
 * Answers that mean this person should not be run.
 *
 * Somebody who never reads a diff cannot answer the questions this study is
 * built on. The page does not say which answer excludes, or the answer stops
 * being honest — the dashboard flags it instead, before the session rather than
 * after it.
 */
export const EXCLUDING = Object.freeze({ readsDiff: ['Never'] });

export function shouldExclude(answers) {
    return Object.entries(EXCLUDING)
        .some(([id, bad]) => bad.includes((answers || {})[id]));
}

// ── scales ───────────────────────────────────────────────────────────────────

export const AGREE = Object.freeze({
    min: 1, max: 7, lowLabel: 'Strongly disagree', highLabel: 'Strongly agree',
});

export const AMOUNT = Object.freeze({
    min: 1, max: 7, lowLabel: 'Very low', highLabel: 'Very high',
});

// ── the constructs ───────────────────────────────────────────────────────────

/**
 * The groups the figures draw, in the order they are asked and plotted.
 *
 * `rq` is which research question the block speaks to. `standard` marks a
 * published instrument, which the analysis scores by its own published rule
 * rather than by averaging our way.
 */
export const CONSTRUCTS = Object.freeze([
    { id: 'umux', title: 'Usability', standard: 'UMUX-Lite', rq: null,
      note: 'Two items, scored as published. Comparable to other papers, which is the whole reason they are here.' },
    { id: 'load', title: 'Workload', standard: 'NASA-TLX', rq: null, scale: AMOUNT,
      note: 'Raw TLX, all six items, unweighted. Physical demand is uninformative for seated work and is reported without interpretation, because dropping an item from a validated instrument costs more than carrying a dull one.' },
    { id: 'control', title: 'Understanding and control', rq: 'RQ1',
      note: 'Whether the decisions passed through the person, and whether they knew what had happened.' },
    { id: 'align', title: 'Alignment', rq: 'RQ3',
      note: 'Whether the result matched the intent, and whether the person came away understanding the codebase better.' },
    { id: 'doc', title: 'The written description', rq: 'RQ2',
      note: 'The core of the thesis. Whether the description stayed true, what keeping it cost, and whether it would still be worth anything later.' },
    { id: 'review', title: 'Review and trust', rq: 'RQ2',
      note: 'What checking cost, and whether confidence was placed where it was earned.' },
]);

/**
 * The items, asked after each condition.
 *
 * Reverse-keyed items are stored exactly as answered and flipped once during
 * analysis. Flipping on the way in would leave the stored data no longer
 * matching what the person saw, and nobody could ever check it.
 */
export const AFTER_CONDITION = Object.freeze([
    // UMUX-Lite, verbatim.
    { id: 'umux1', c: 'umux', text: "This system's capabilities meet my requirements." },
    { id: 'umux2', c: 'umux', text: 'This system is easy to use.' },

    // NASA-TLX, raw, verbatim, on the amount scale.
    { id: 'tlxMental', c: 'load', text: 'How mentally demanding was the task?' },
    { id: 'tlxPhysical', c: 'load', text: 'How physically demanding was the task?' },
    { id: 'tlxPace', c: 'load', text: 'How hurried or rushed was the pace of the task?' },
    { id: 'tlxSuccess', c: 'load', text: 'How successful were you in accomplishing what you were asked to do?', reverse: true },
    { id: 'tlxEffort', c: 'load', text: 'How hard did you have to work to accomplish your level of performance?' },
    { id: 'tlxFrustration', c: 'load', text: 'How insecure, discouraged, irritated, stressed, and annoyed were you?' },

    // Understanding and control.
    { id: 'ctl1', c: 'control', text: 'I always knew what the agent had changed, and why.' },
    { id: 'ctl2', c: 'control', text: 'I could steer the work toward what I wanted.' },
    { id: 'ctl3', c: 'control', text: 'I felt in control of the overall editing process.' },
    { id: 'ctl4', c: 'control', text: 'I lost track of the state of the codebase while the agent worked.', reverse: true },
    { id: 'ctl5', c: 'control', text: 'The agent made decisions that were mine to make.', reverse: true },

    // Alignment.
    { id: 'ali1', c: 'align', text: 'What the agent produced matched what I intended.' },
    { id: 'ali2', c: 'align', text: 'This way of working helped me build a clearer picture of the codebase.' },
    { id: 'ali3', c: 'align', text: 'I could move between changing code and checking it without losing my place.' },

    // The written description.
    { id: 'doc1', c: 'doc', text: 'Whenever I checked, the written description matched the code.' },
    { id: 'doc2', c: 'doc', text: 'Keeping the written description current felt like busywork.', reverse: true },
    { id: 'doc3', c: 'doc', text: 'The effort I spent writing things down paid off within this session.' },
    { id: 'doc4', c: 'doc', text: 'If I came back in a month, what is written down would get me back up to speed.' },
    { id: 'doc5', c: 'doc', text: 'When I needed to know why something was built a certain way, I could find out quickly.' },

    // Review and trust.
    { id: 'rev1', c: 'review', text: 'I was confident the code produced was correct.' },
    { id: 'rev2', c: 'review', text: 'I could reject or change anything I disagreed with.' },
    { id: 'rev3', c: 'review', text: 'I accepted changes I had not really reviewed.', reverse: true },
    { id: 'rev4', c: 'review', text: 'I could tell which parts of the result I still needed to check.' },
]);

/** Which scale an item is answered on. */
export function scaleFor(item) {
    const c = CONSTRUCTS.find((x) => x.id === item.c);
    return (c && c.scale) || AGREE;
}

/** A rating turned the way the construct reads, for scoring only. */
export function keyed(item, value) {
    if (value == null) return null;
    const s = scaleFor(item);
    return item.reverse ? s.min + s.max - value : value;
}

/**
 * Asked in both conditions, to check the manipulation landed.
 *
 * If somebody in the codoc condition never noticed the description changing,
 * their answers are about a tool they did not meet, and the analysis has to know
 * that rather than average it in.
 */
export const MANIPULATION_CHECK = Object.freeze([
    { id: 'noticedChange', type: 'choice',
      label: 'Did you notice the written description changing while the agent worked',
      options: ['Yes, often', 'Once or twice', 'No'] },
    { id: 'openQ', type: 'longtext',
      label: 'Anything about this way of working that helped or got in the way',
      placeholder: 'A sentence or two. This is the answer we quote in the paper.' },
]);

/**
 * The sign-off, asked of the participant rather than transcribed by the
 * researcher.
 *
 * It used to be typed into the dashboard while they spoke, which made it a
 * record of how well somebody explained themselves and how fast the researcher
 * could type. Asked here it is their own words, in their own time, and it is the
 * same words for everybody.
 */
export const SIGNOFF = Object.freeze([
    { id: 'correct', type: 'choice',
      label: 'Is the change you just made correct and complete?',
      options: ['Yes', 'Mostly', 'Not sure', 'No'] },
    { id: 'confidence', type: 'scale5',
      label: 'How confident are you in that answer',
      low: 'Not at all', high: 'Completely' },
    { id: 'grounds', type: 'multi',
      label: 'What is that resting on? Pick everything that applies.',
      options: ['I ran the tests', 'I read the diff', 'I read the description',
                'The agent said it was done', 'I ran the project and looked at the output'] },
    { id: 'unsure', type: 'longtext',
      label: 'Is there any part of it you are less sure about?',
      placeholder: 'A sentence is enough, and "no" is a real answer.' },
]);

/** Asked once at the very end, with both conditions done. */
export const SCENARIOS = Object.freeze([
    { id: 's1', text: 'Fixing a typo in a repository you have never seen' },
    { id: 's2', text: 'A feature across several modules, in a codebase you will own for a year' },
    { id: 's3', text: 'A throwaway script you will delete tomorrow' },
    { id: 's4', text: 'Getting a new teammate up to speed on this codebase' },
    { id: 's5', text: 'A production hotfix, under time pressure' },
]);

/**
 * The closing interview, in three parts.
 *
 * Written down so it is asked the same way every time. The researcher still
 * follows up on whatever an answer opens up — that is the point of doing it
 * live — but the openings are fixed, and each one is here because it speaks to
 * a research question rather than because it seemed interesting to ask.
 *
 * On the page as well as on the call: somebody who has just spent two hours
 * often writes a sharper answer than they say, and the writing gives the
 * researcher something to follow up on rather than something to transcribe.
 */
export const INTERVIEW = Object.freeze([
    {
        id: 'comparison',
        title: 'Comparing the two',
        questions: [
            { id: 'workflow', rq: 'RQ1, RQ2',
              label: 'How did the way you worked differ between the two — both in understanding the codebase and in making changes to it?' },
            { id: 'strategy', rq: 'RQ2',
              label: 'Did you go about editing differently in each? If so, why — was it about staying in control, or something else?' },
            { id: 'tracking', rq: 'RQ1',
              label: 'Which one made it easier to keep track of changes across the codebase?' },
            { id: 'keepingUp', rq: 'RQ1',
              label: 'The agent changes things quickly. In which one could you keep up with what had changed?' },
            { id: 'thinking', rq: 'RQ1',
              label: 'Did having the description in a different shape — a chat, or a tree of features — change how you thought about the codebase, or how you talked to the agent?' },
        ],
    },
    {
        id: 'trust',
        title: 'Trust, and disagreeing',
        questions: [
            { id: 'whyChanged', rq: 'RQ1',
              label: 'Did you understand why the agent made the changes it made?' },
            { id: 'disagreed', rq: 'RQ2',
              label: 'Was there a point where you disagreed with it? What did each way of working give you to settle that?' },
            { id: 'verified', rq: 'RQ2',
              label: 'How did you check that what it did was what you meant?' },
        ],
    },
    {
        id: 'adoption',
        title: 'Whether you would use it',
        questions: [
            { id: 'fit', rq: null,
              label: 'Where would something like codoc fit in the work you actually do?' },
            { id: 'blocking', rq: null,
              label: 'What would have to be different before you would use it day to day?' },
            { id: 'prefer', rq: null,
              label: 'What would make you pick one over the other?' },
        ],
    },
]);

/** Flat, for the page and for checking nothing is missed. */
export const INTERVIEW_QUESTIONS = Object.freeze(
    INTERVIEW.flatMap((part) => part.questions.map((q) => ({ ...q, part: part.id }))));
