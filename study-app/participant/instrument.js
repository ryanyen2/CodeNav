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

/**
 * Seven points, labelled at the ends, with a middle.
 *
 * Seven rather than five because the response-options literature puts the two
 * close together and seven marginally ahead (Preston & Colman 2000; Krosnick &
 * Presser), and because UMUX-Lite was published on seven. An instrument
 * reproduced on a different number of points is no longer the instrument whose
 * norms make it worth carrying.
 */
export const AGREE = Object.freeze({
    min: 1, max: 7, step: 1, lowLabel: 'Strongly disagree', highLabel: 'Strongly agree',
});

/**
 * NASA-TLX's own scale: 0 to 100 in steps of 5, drawn as 21 tick marks.
 *
 * Deliberately not the seven points every other block uses. Lee et al. (TOCHI
 * 2026) compared CHI papers that collected TLX on 5 or 7 points against those
 * that kept the original 21, and the coarse ones do not merely lose precision:
 * they move subscales between factors. On coarse scales frustration loads on
 * the PHYSICAL factor (.774) rather than the mental one (.247) and effort
 * splits across both; on the original scale effort (.930) and frustration
 * (.765) sit where the instrument says they belong. Matching the page's seven
 * points would have bought consistency on screen and paid for it in the one
 * block whose numbers are meant to be compared with other papers.
 */
export const AMOUNT = Object.freeze({
    min: 0, max: 100, step: 5, lowLabel: 'Very low', highLabel: 'Very high',
});

/**
 * The same 21 points, labelled for performance.
 *
 * TLX asks all six subscales so that a HIGHER answer means MORE workload, which
 * for performance means the high end has to be failure. People read a high
 * number as "I did well" regardless, and that one misreading is the most common
 * way the instrument is broken in print: an easy task rated 100 on performance
 * and 0 on everything else averages to the same score as a hard task rated 0 on
 * performance and 20 on everything else.
 *
 * So this item is asked the way it is already read — Failure at the low end,
 * Perfect at the high end, which are Hart and Staveland's own endpoint words —
 * and flipped once during scoring. That is the second of the two
 * implementations Lee et al. accept, and it is the one whose failure mode
 * belongs to us rather than to the participant: `rtlx` is the only thing that
 * averages these six, and it flips through `keyed`.
 */
export const PERFORMANCE = Object.freeze({
    min: 0, max: 100, step: 5, lowLabel: 'Failure', highLabel: 'Perfect',
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
    // The rq tags below name the CURRENT questions (docs/plans/2026-08-16-001):
    //   RQ1 — understanding: can codoc help somebody build a theory of the program
    //   RQ2 — authored modification: do the decisions pass through the person
    // They used to carry the numbering of the abandoned three-RQ design, where
    // RQ1 was co-authorship and RQ3 was understanding. Same labels, different
    // referents, in a file read beside the analysis plan — so a block tagged RQ1
    // meant one thing here and another there.
    { id: 'load', title: 'Workload', standard: 'NASA-TLX', rq: null, scale: AMOUNT,
      note: 'Raw TLX, all six items, unweighted, on the original 21-point 0–100 scale. Physical demand is uninformative for seated work and is reported without interpretation, because dropping an item from a validated instrument costs more than carrying a dull one. Unweighted is the norm and is defensible here because the design compares two conditions; the pairwise weighting step earns its minutes only in single-task studies, which this is not.' },
    { id: 'control', title: 'Understanding and control', rq: 'RQ2',
      note: 'Whether the decisions passed through the person, and whether they knew what had happened.' },
    { id: 'align', title: 'Alignment', rq: 'RQ1',
      note: 'Whether the result matched the intent, and whether the person came away understanding the codebase better.' },
    { id: 'doc', title: 'The written description', rq: 'RQ1',
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
    // UMUX-Lite, verbatim. Published as "[This system's] capabilities meet my
    // requirements" — the brackets are the instrument's own instruction to name
    // the thing being rated, and here the thing is a way of working rather than
    // a program. Both conditions run the same agent in the same editor, so
    // "this system" would have been answered about VS Code by some people and
    // about the workflow by others, and nothing downstream could tell which.
    { id: 'umux1', c: 'umux', text: "This workflow's capabilities meet my requirements." },
    { id: 'umux2', c: 'umux', text: 'This workflow is easy to use.' },

    // NASA-TLX, raw, all six, on the original 21-point scale. `title` and
    // `description` are Hart and Staveland's own, shown on screen rather than
    // kept in a manual: the subscales correlate strongly enough in HCI work that
    // a participant reading only the short question answers four of them the
    // same way, and the definitions are what pull them apart.
    { id: 'tlxMental', c: 'load', title: 'Mental demand',
      text: 'How mentally demanding was the task?',
      description: 'How much mental and perceptual activity was required (thinking, deciding, calculating, remembering, looking, searching)? Was the task easy or demanding, simple or complex, exacting or forgiving?' },
    { id: 'tlxPhysical', c: 'load', title: 'Physical demand',
      text: 'How physically demanding was the task?',
      description: 'How much physical activity was required (typing, pointing, scrolling, moving between windows)? Was the task easy or demanding, slack or strenuous, restful or laborious?' },
    { id: 'tlxPace', c: 'load', title: 'Temporal demand',
      text: 'How hurried or rushed was the pace of the task?',
      description: 'How much time pressure did you feel because of the rate or pace at which things happened? Was the pace slow and leisurely, or rapid and frantic?' },
    { id: 'tlxSuccess', c: 'load', title: 'Performance', scale: PERFORMANCE, reverse: true,
      text: 'How successful were you in accomplishing what you were asked to do?',
      description: 'How successful do you think you were in doing what you were asked to do? How satisfied are you with how you did it?' },
    { id: 'tlxEffort', c: 'load', title: 'Effort',
      text: 'How hard did you have to work to accomplish your level of performance?',
      description: 'How hard did you have to work, mentally and physically, to reach the level of performance you reached?' },
    { id: 'tlxFrustration', c: 'load', title: 'Frustration',
      text: 'How insecure, discouraged, irritated, stressed, and annoyed were you?',
      description: 'How insecure, discouraged, irritated, stressed and annoyed — versus secure, gratified, content and relaxed — did you feel during the task?' },

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

/**
 * Which scale an item is answered on.
 *
 * The item wins over its construct, because one item — TLX's performance — is
 * answered on the same 21 points as its neighbours but with different words at
 * the ends.
 */
export function scaleFor(item) {
    if (item.scale) return item.scale;
    const c = CONSTRUCTS.find((x) => x.id === item.c);
    return (c && c.scale) || AGREE;
}

/** A rating turned the way the construct reads, for scoring only. */
export function keyed(item, value) {
    if (value == null) return null;
    const s = scaleFor(item);
    return item.reverse ? s.min + s.max - value : value;
}

/** A rating as a share of its own scale, 0–100, so two scales can be compared. */
export function normalized(item, value) {
    const k = keyed(item, value);
    if (k == null) return null;
    const s = scaleFor(item);
    return ((k - s.min) / (s.max - s.min)) * 100;
}

const itemsIn = (c) => AFTER_CONDITION.filter((q) => q.c === c);

/**
 * Raw TLX for one condition: the six subscales, unweighted, on 0–100.
 *
 * This function is the only place the six are ever averaged, and it is the
 * reason it exists. Performance is collected the way people read it and has to
 * be flipped before it joins the other five; a flip that lives only in a note
 * gets forgotten by whoever writes the analysis, and the number that comes out
 * of forgetting looks completely ordinary. Routing every average through
 * `keyed` makes the flip impossible to skip rather than merely documented.
 *
 * Returns null unless all six were answered. Five of six is not a raw TLX, and
 * averaging what arrived would quietly rescale that participant against the
 * others.
 */
export function rtlx(answers) {
    const items = itemsIn('load');
    const scores = {};
    for (const q of items) {
        const v = normalized(q, (answers || {})[q.id]);
        if (v == null) return null;
        scores[q.id] = v;
    }
    const values = Object.values(scores);
    return { overall: values.reduce((a, b) => a + b, 0) / values.length, subscales: scores };
}

/**
 * UMUX-Lite for one condition, on 0–100, by its published formula.
 *
 * Lewis, Utesch and Maher score it (item1 + item2 − 2) × (100/12) on their
 * seven points, which is the same arithmetic as the mean of the two items
 * expressed as a share of the scale — written the second way here so it stays
 * correct if the scale ever moves, and checked against the published form in
 * the tests.
 *
 * Reported raw. There is a regression that converts this to a SUS-equivalent
 * score, but its constants come from particular corpora, and a study comparing
 * two conditions in the same session gains nothing from the conversion that it
 * does not already have from the difference.
 */
export function umuxLite(answers) {
    const items = itemsIn('umux');
    const values = items.map((q) => normalized(q, (answers || {})[q.id]));
    if (values.some((v) => v == null)) return null;
    return values.reduce((a, b) => a + b, 0) / values.length;
}

/**
 * The mean of a construct's items, keyed, on the scale they were answered on.
 *
 * For the four blocks that are ours rather than published. A construct mean is
 * reported alongside its items, never instead of them: these blocks are three
 * to five items written for this study, so the mean is a summary of what was
 * asked and not evidence that the items measure one thing.
 */
export function constructScore(answers, constructId) {
    const values = itemsIn(constructId)
        .map((q) => keyed(q, (answers || {})[q.id]))
        .filter((v) => v != null);
    if (!values.length) return null;
    return values.reduce((a, b) => a + b, 0) / values.length;
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

/**
 * The one thing asked after the task that is not multiple choice.
 *
 * The questions themselves live in each project's STUDY.md and come through the
 * same extractor as the pre-task quiz, so their answers never ship to a browser.
 * They are per project, because they are about the change the participant just
 * made to THAT codebase, and they have right answers, because a question with no
 * right answer cannot be scored the same way twice.
 *
 * They were four boxes to type in until 2026-08. Freeform got short answers to
 * questions whose value is in the follow-up, at the end of two hours, and nothing
 * comparable between participants. The follow-up now happens in the closing
 * interview, out loud, where it belongs.
 *
 * This scale stays because a fluent reconstruction and a real memory look the
 * same in a set of answers. Somebody who worked it out from first principles in
 * the moment has still not carried it out of the session, and only they can say
 * which it was.
 */
export const REFLECTION = Object.freeze([
    { id: 'recall', type: 'scale5',
      label: 'How much of that were you sure of, rather than working out just now',
      low: 'Working it out', high: 'Sure of it' },
]);

/**
 * Asked once at the very end, with both conditions done.
 *
 * Each item is a KIND OF WORK, described in a whole sentence. The earlier list
 * was noun phrases like "a throwaway script you will delete tomorrow", and it
 * mixed three different things — how big the job is, how long you will own the
 * code, and how much of a hurry you are in — so an answer could not be read as
 * being about any one of them.
 *
 * The activities are the ones empirical studies of developer work keep
 * reporting, rather than ones chosen here:
 *
 *   Meyer, Fritz, Murphy & Zimmermann, "Software developers' perceptions of
 *   productivity", FSE 2014 — 379 surveyed and 11 observed; the observed
 *   developers spent 32.3% of their time coding and 3.9% debugging, so a list
 *   weighted toward debugging would not describe the working day.
 *
 *   Meyer, Fritz, Murphy & Zimmermann, "The Work Life of Developers: Activities,
 *   Switches and Perceived Productivity", IEEE TSE, 2017.
 *
 *   LaToza, Venolia & DeLine, "Maintaining mental models: a study of developer
 *   work habits", ICSE 2006 — the finding this study is built on, that the
 *   knowledge of why code is the way it is lives in memory and is recovered by
 *   reading code and interrupting colleagues.
 *
 *   Sillito, Murphy & De Volder, "Asking and Answering Questions during a
 *   Programming Change Task", IEEE TSE 34(4), 2008 — the 44 questions a
 *   programmer asks while making a change.
 *
 * THREE OF THE SEVEN ARE CASES A WRITTEN DESCRIPTION PLAUSIBLY DOES NOT HELP
 * WITH, and they are here on purpose. Debugging a reproducible fault is answered
 * by execution rather than by intent; new code in a new file has nothing yet to
 * describe; and an hour-long fix is exactly where keeping a description current
 * is overhead. A list on which the tool could only win would measure the list.
 */
export const SCENARIOS = Object.freeze([
    { id: 's1', text: 'Adding a feature to code somebody else wrote' },
    { id: 's2', text: 'Changing how something already works, when other parts of the code depend on it' },
    { id: 's3', text: 'Tracking down a bug you can already reproduce' },
    { id: 's4', text: 'Writing something new, in a file that does not exist yet' },
    { id: 's5', text: 'Reviewing a change somebody else made, and deciding whether to approve it' },
    { id: 's6', text: 'Coming back to a project you have not opened in six months' },
    { id: 's7', text: 'A small fix you have to ship within the hour' },
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
