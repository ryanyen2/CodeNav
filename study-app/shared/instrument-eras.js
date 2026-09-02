// When the instrument changed, and what each change costs the analysis.
//
// The study ran while the study was still being fixed. That is ordinary and it
// is survivable, but only if every change is written down as an INSTANT and
// every measure says which instants break it. Otherwise the analysis pools two
// questions that were not the same question, and nothing downstream can see it:
// `keyed` in instrument.js reads today's reverse flags, `constructScore`
// averages whatever ids it finds, and both produce a perfectly ordinary-looking
// number from answers that mean opposite things.
//
// So this file is the second half of instrument.js. That one says what is asked
// TODAY; this one says what was asked THEN, and which pairs of thens can be
// added together.
//
// ── the rule this file follows ───────────────────────────────────────────────
//
// A boundary carries the COMMIT time, not a date, because a date cannot decide a
// session that ran the same day. And a commit is not a deploy: the participant
// saw whatever was on codoc-11b10.web.app, which changed when somebody ran
// `npm run build && firebase deploy`, and nothing in this repository records
// when that happened. Firebase's console keeps a release history and it is the
// only source for it.
//
// Rather than guess, a boundary states the commit instant and an `uncertainUntil`
// after it. A session inside that window is reported as UNPLACEABLE rather than
// assigned to a side. Assigning it would be the same error as crediting an
// unattributable span to the nearest editor, and it would be invisible.

/** A day. Deploys in this study followed the commit within hours, never longer. */
const DEPLOY_WINDOW_MS = 24 * 60 * 60 * 1000;

const ms = (iso) => Date.parse(iso);

/**
 * Every change that moved what a participant was asked or shown, in order.
 *
 * `breaks` names the measure ids this boundary makes unpoolable across it.
 * `recoverable` means the earlier answers can be brought onto today's footing by
 * a stated transform, and `transform` says what it is. Everything else is a
 * clean break: there is no answer to recover.
 */
export const BOUNDARIES = Object.freeze([
    {
        id: 'task-becomes-review',
        at: ms('2026-08-19T01:25:04-04:00'),
        uncertainUntil: ms('2026-08-19T01:25:04-04:00') + DEPLOY_WINDOW_MS,
        commit: '89fef58',
        title: 'The task became reviewing a recorded change, and the pre-task quiz went',
        changes: [
            'The task changed from implementing a change to reviewing one the agent had already made, played back from a recording.',
            'The open-book round of five questions before the task was dropped. Nothing writes answers/quiz-<project>-before from here on.',
        ],
        breaks: ['quiz.before'],
        recoverable: false,
        // The task itself changed, so this is the widest boundary in the file.
        // Anything about how the participant WORKED is a different activity
        // either side of it, not the same activity measured twice.
        breaksEverythingBehavioural: true,
    },
    {
        id: 'commands-named',
        at: ms('2026-08-20T16:05:21-04:00'),
        uncertainUntil: ms('2026-08-20T20:08:53-04:00') + DEPLOY_WINDOW_MS,
        commit: '50530df..1bd914a',
        title: 'The page named the commands to run, the planted problems went from two to one, and the scenarios were rewritten',
        changes: [
            'The task page began naming the commands to run once the change is in, so "found by running" became a route the page prompted rather than one the participant thought of.',
            'One planted problem per project instead of two, so a project maximum changed and coverage proportions are against a different denominator.',
            'The seven scenario items s1..s7 were rewritten from noun phrases to whole sentences. Same ids, different stimulus.',
            'The four non-published Likert blocks were reworded. Every id and every reverse key unchanged, so those items still line up.',
        ],
        breaks: ['detection.coverage', 'detection.route', 'scenarios'],
        recoverable: false,
    },
    {
        id: 'ctl4-flip',
        at: ms('2026-08-22T18:05:19-04:00'),
        uncertainUntil: ms('2026-08-22T18:05:19-04:00') + DEPLOY_WINDOW_MS,
        commit: '33e0f67',
        title: 'ctl4 changed polarity, and the reflect step and the manipulation check were removed',
        changes: [
            'ctl4 went from "I lost track of what the code looked like while the agent worked" (reverse-keyed) to "I could keep track of the code while the agent was making changes" (not reversed). Same id, opposite meaning.',
            'The whole reflect step went: five closed-book questions and the recall scale. Nothing writes answers/reflect-<condition> from here on.',
            'The "In your own words" section went, and the manipulation check noticedChange went with it. There is no longer a stored way to identify a codoc participant who never saw the description move.',
        ],
        breaks: ['ctl4', 'reflect', 'recall', 'noticedChange', 'openQ'],
        // ctl4 alone is recoverable, and it is the only item in this study that
        // is. Both wordings are on the same seven points and ask about the same
        // thing, so an earlier answer is brought onto today's footing by the
        // scale's own reflection. What that does NOT recover is the wording: a
        // negated item and its positive twin are not interchangeable in the
        // acquiescence literature, so the transform is a defensible decision to
        // report, never a repair to apply silently.
        recoverable: ['ctl4'],
        transform: { ctl4: 'v => 8 - v   // 1..7 reflected; the earlier item was reverse-keyed' },
    },
    {
        id: 'fault-handed-over',
        at: ms('2026-08-24T19:55:35-04:00'),
        uncertainUntil: ms('2026-08-24T19:55:35-04:00') + DEPLOY_WINDOW_MS,
        commit: 'd3ffe54',
        title: 'The task page began stating the fault outright',
        changes: [
            'The page now prints what the commands produce after the change, marks which moved line was asked for and which was not, says what the agent broke, shows the broken output, and gives four numbered steps.',
            'From here the participant is HANDED the finding, so detection is no longer something the task measures.',
        ],
        breaks: ['detection.coverage', 'detection.time', 'detection.route'],
        recoverable: false,
        // Worth reading before trusting the date on this one. analysis-plan.md
        // heads this section "The page states the fault, from 2026-08-23", but
        // the prose that states it (prose.js, repl.after.expected and
        // repl.after.fault) first appears in d3ffe54, committed on 2026-08-24 at
        // 19:55 EDT, and that same commit is the one that wrote the 08-23
        // heading. So the plan's date is a day earlier than the code's.
        //
        // This is the single most valuable thing in this file to settle, because
        // detection coverage is one of the two PRE-REGISTERED confirmatory
        // predictions. If it went on 08-23 it is dead for anybody run that day;
        // if it went when the code says, every session that finished before the
        // evening of 08-24 still measures it. One day decides whether a
        // confirmatory claim survives.
        //
        // Settle it against Firebase Hosting's release history for
        // codoc-11b10 (console → Hosting → Release history), which is the only
        // record of when a build actually reached a participant.
        contested: {
            planSays: '2026-08-23',
            codeSays: '2026-08-24T19:55:35-04:00',
            settleWith: 'Firebase Hosting release history for codoc-11b10',
        },
    },
    {
        id: 'replay-wiped-instrumentation',
        // A WINDOW, not a cut. The replay harness arrives with the task change on
        // 08-19 and the wipe arrives with it; the fix lands on 08-24. Sessions
        // before the window predate the replay entirely and are not at risk, so
        // an open-ended "before this" would condemn the whole study including
        // the sessions that never ran the code.
        from: ms('2026-08-19T01:25:04-04:00'),
        at: ms('2026-08-24T19:28:18-04:00'),
        uncertainUntil: ms('2026-08-24T19:55:35-04:00') + DEPLOY_WINDOW_MS,
        commit: '0a47746..d3ffe54',
        title: 'The replay deleted the session\'s own instrumentation',
        changes: [
            'start-session deleted .vscode/settings.json and .claude/settings.json and re-pointed .mcp.json at the seeding machine, in BOTH arms.',
            'The effect is a session that ran with the logger off, no prompt records, and codoc\'s MCP not connected.',
            'setup.sh --check runs BEFORE the replay, so it can never catch this class of fault.',
        ],
        // This one runs the other way round: it is a DEFECT boundary, and the
        // sessions at risk are the ones INSIDE the window rather than after it.
        // It is here because deciding which sessions are analysable is the same
        // job as deciding which measures pool, and a session with no interaction
        // log fails every measure computed from one no matter which era it sits
        // in. Being flagged is not a verdict: the fault did not fire on every
        // run, so a flagged session is one to check against its own export,
        // which is what `has` in the report is for.
        direction: 'window',
        breaks: ['interaction.log', 'prompts', 'codoc.mcp'],
        recoverable: false,
    },
]);

/** The boundaries that actually cut a timeline, oldest first. */
const CUTS = Object.freeze(
    BOUNDARIES.filter((b) => !b.direction).sort((a, b) => a.at - b.at));

/**
 * The eras, which are just the gaps between the cuts.
 *
 * Named by the date they open on rather than numbered, because a number in a
 * results table tells a reader nothing and a date sends them to this file.
 */
export const ERAS = Object.freeze(
    CUTS.reduce((acc, cut, i) => {
        acc.push({
            id: `era-${i}`,
            from: i === 0 ? null : CUTS[i - 1].at,
            to: cut.at,
            openedBy: i === 0 ? null : CUTS[i - 1].id,
            closedBy: cut.id,
        });
        if (i === CUTS.length - 1) {
            acc.push({
                id: `era-${i + 1}`,
                from: cut.at,
                to: null,
                openedBy: cut.id,
                closedBy: null,
            });
        }
        return acc;
    }, []));

/**
 * Which era an instant falls in, and whether it can be placed at all.
 *
 * Returns `{ era, unplaceable, near }`. `unplaceable` is set when the instant
 * sits inside a boundary's deploy window, and `near` names the boundary. A
 * caller must not treat an unplaceable session as belonging to either side; the
 * point of the flag is that it has to be resolved by hand, against the hosting
 * release history or the researcher's own notes.
 */
export function eraFor(atMs) {
    if (atMs == null || !Number.isFinite(atMs)) {
        return { era: null, unplaceable: true, near: null, why: 'no timestamp' };
    }
    const near = CUTS.find((b) => atMs >= b.at && atMs < b.uncertainUntil);
    const era = ERAS.find((e) => (e.from == null || atMs >= e.from) && (e.to == null || atMs < e.to));
    return {
        era: era || null,
        unplaceable: !!near,
        near: near ? near.id : null,
        why: near ? `within the deploy window of ${near.id}` : null,
    };
}

/**
 * The defect windows a session falls inside, which flag it rather than place it.
 *
 * A defect does not cut the timeline, so it never changes which era a session
 * belongs to. It changes whether the session's own recording can be believed,
 * which is a separate question that has to be asked of every era alike.
 */
export function defectsFor(atMs) {
    if (atMs == null) return [];
    return BOUNDARIES
        .filter((b) => b.direction === 'window')
        .filter((b) => atMs >= b.from && atMs < b.uncertainUntil)
        .map((b) => b.id);
}

/**
 * Every measure the analysis plan names, and where it comes from.
 *
 * `doc` is the answers document it is stored in, or null when the measure is
 * computed from something other than the participant's page. `confirmatory`
 * marks the two pre-registered predictions, because those are the ones where a
 * pooling decision is not the analyst's to make quietly.
 */
export const MEASURES = Object.freeze([
    // The two published blocks. Untouched throughout, which is exactly the value
    // of using published instruments in a study that had to keep changing.
    { id: 'umux', title: 'UMUX-Lite', items: ['umux1', 'umux2'], doc: 'after-<condition>', brokenBy: [] },
    { id: 'tlx', title: 'NASA-TLX (raw)', doc: 'after-<condition>', brokenBy: [],
      items: ['tlxMental', 'tlxPhysical', 'tlxPace', 'tlxSuccess', 'tlxEffort', 'tlxFrustration'] },

    // Ours. Reworded twice, but ids and reverse keys held except for ctl4.
    { id: 'control', title: 'Understanding and control', doc: 'after-<condition>',
      items: ['ctl1', 'ctl2', 'ctl3', 'ctl5'], brokenBy: [],
      note: 'ctl4 is deliberately not in this list. The construct mean must be computed without it unless the ctl4 decision is taken and stated.' },
    { id: 'ctl4', title: 'ctl4 on its own', doc: 'after-<condition>', items: ['ctl4'],
      brokenBy: ['ctl4-flip'], recoverable: true },
    { id: 'align', title: 'Alignment', doc: 'after-<condition>', items: ['ali1', 'ali2', 'ali3'], brokenBy: [] },
    { id: 'doc', title: 'The written description', doc: 'after-<condition>',
      items: ['doc1', 'doc2', 'doc3', 'doc4', 'doc5'], brokenBy: [] },
    { id: 'review', title: 'Review and trust', doc: 'after-<condition>',
      items: ['rev1', 'rev2', 'rev3', 'rev4'], brokenBy: [] },

    // Gone.
    { id: 'noticedChange', title: 'Manipulation check', doc: 'after-<condition>',
      items: ['noticedChange'], brokenBy: ['ctl4-flip'], gone: true },
    { id: 'openQ', title: 'In your own words', doc: 'after-<condition>', items: ['openQ'],
      brokenBy: ['ctl4-flip'], gone: true },
    { id: 'reflect', title: 'Closed-book round after the task', doc: 'reflect-<condition>',
      brokenBy: ['ctl4-flip'], gone: true },
    { id: 'recall', title: 'Recall scale', doc: 'reflect-<condition>', items: ['recall'],
      brokenBy: ['ctl4-flip'], gone: true },
    { id: 'quiz.before', title: 'Open-book round before the task', doc: 'quiz-<project>-before',
      brokenBy: ['task-becomes-review'], gone: true },

    // Stable throughout.
    { id: 'prestudy', title: 'Background and experience', doc: 'prestudy', brokenBy: [] },
    { id: 'signoff', title: 'Sign-off', doc: 'signoff-<condition>',
      items: ['correct', 'confidence', 'grounds', 'unsure'], brokenBy: [],
      note: 'Moved from the dashboard to the page on 2026-08-16, before the first session, so nothing straddles it.' },
    { id: 'task.clock', title: 'Task start and finish', doc: 'task-<condition>',
      items: ['startedAt', 'finishedAt', 'elapsedMs'], brokenBy: [] },

    // Rewritten stimulus on stable ids, which is the quiet one.
    { id: 'scenarios', title: 'Which kind of work would you use this for', doc: 'scenarios',
      items: ['s1', 's2', 's3', 's4', 's5', 's6', 's7'], brokenBy: ['commands-named'],
      note: 'The ids did not move and the text did, so nothing downstream can detect this. An answer to a noun phrase and an answer to a sentence are not the same answer.' },

    // Computed elsewhere, and the two that the paper actually promised.
    { id: 'detection.coverage', title: 'Detection coverage', doc: null, confirmatory: true,
      brokenBy: ['commands-named', 'fault-handed-over'],
      note: 'Rated in the dashboard and again by a blind rater. The 08-20 boundary raises the floor; the 08-24 one hands the participant the answer.' },
    { id: 'detection.time', title: 'Time to first correct detection', doc: null,
      brokenBy: ['fault-handed-over'] },
    { id: 'detection.route', title: 'Route to each detection', doc: null,
      brokenBy: ['commands-named', 'fault-handed-over'] },
    { id: 'trace.durable', title: 'A durable written trace, per problem', doc: null, confirmatory: true,
      brokenBy: [],
      note: 'Explicitly untouched by the 08-24 change, per analysis-plan.md. This is the confirmatory prediction that survives everything.' },
    { id: 'record.truth', title: 'Is the record true at the end', doc: null, brokenBy: [],
      note: 'Pre-registered as predicted NOT to differ.' },
    { id: 'interaction.log', title: 'The interaction log', doc: null, brokenBy: [],
      note: 'Not broken by an instrument change, but absent entirely for any session that ran before the 08-24 replay fix.' },
]);

/**
 * Whether one measure survives being pooled across a set of eras.
 *
 * `eras` is the set of era ids the participants actually fall in. A measure is
 * poolable when no boundary between the earliest and the latest of them is one
 * that breaks it.
 */
export function poolable(measureId, eraIds) {
    const m = MEASURES.find((x) => x.id === measureId);
    if (!m) return { ok: false, why: `no measure called ${measureId}` };
    const ids = [...new Set(eraIds)].filter(Boolean);
    if (ids.length <= 1) return { ok: true, why: 'every session is in one era' };

    const order = ERAS.map((e) => e.id);
    const first = Math.min(...ids.map((i) => order.indexOf(i)));
    const last = Math.max(...ids.map((i) => order.indexOf(i)));
    // The cuts that lie between the earliest and latest era present.
    const crossed = CUTS.slice(first, last).map((c) => c.id);
    const fatal = crossed.filter((c) => (m.brokenBy || []).includes(c));

    if (!fatal.length) return { ok: true, why: `crosses ${crossed.join(', ') || 'nothing'}, none of which touch it` };
    return {
        ok: false,
        why: `broken by ${fatal.join(', ')}`,
        recoverable: !!m.recoverable,
        confirmatory: !!m.confirmatory,
    };
}
