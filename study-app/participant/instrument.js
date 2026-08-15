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
 * In Google rather than on this page because it asks for gender and age, and
 * those belong with consent rather than in the study database beside a session
 * log. The cost is that these answers cannot be joined automatically: they are
 * keyed by the participant code typed into the form's first field, and joined by
 * hand at analysis. That is the trade we want for this particular data.
 */
export const PRESTUDY_FORM =
    'https://docs.google.com/forms/d/e/1FAIpQLSeWiRCuv3ZlcGrNKoOy_HKzfs9SUsMaCZ1B9RZ-bF0eB8IpzA/viewform?embedded=true';

/**
 * The one background item that stays on this page.
 *
 * It is the screening question, and the dashboard has to be able to see it: a
 * person who never reads a diff cannot answer the questions this study is built
 * on, and finding that out after the session is finding it out too late. The
 * page does not say that it excludes, or the answer stops being honest.
 */
export const SCREENING = Object.freeze([
    { id: 'readsDiff', type: 'choice',
      label: 'When an agent proposes a change across several files, how often do you read the diff before accepting',
      options: ['Always', 'Usually', 'About half the time', 'Rarely', 'Never'] },
]);

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

/** Asked once at the very end, with both conditions done. */
export const SCENARIOS = Object.freeze([
    { id: 's1', text: 'Fixing a typo in a repository you have never seen' },
    { id: 's2', text: 'A feature across several modules, in a codebase you will own for a year' },
    { id: 's3', text: 'A throwaway script you will delete tomorrow' },
    { id: 's4', text: 'Getting a new teammate up to speed on this codebase' },
    { id: 's5', text: 'A production hotfix, under time pressure' },
]);

/** The closing free-text, which is where the unexpected findings come from. */
export const DEBRIEF = Object.freeze([
    { id: 'differed', type: 'longtext',
      label: 'What was the biggest difference between the two ways of working' },
    { id: 'wouldKeep', type: 'longtext',
      label: 'Is there anything from either one you would want in your own work' },
]);
