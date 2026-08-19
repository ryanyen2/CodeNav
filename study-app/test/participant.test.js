// The participant flow: what gets asked, in what order, and what is stored.
//
//   node --test test/participant.test.js
import test, { before } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import {
    PRESTUDY, REQUIRED, AFTER_CONDITION, CONSTRUCTS, AGREE, AMOUNT, PERFORMANCE,
    scaleFor, keyed, normalized, rtlx, umuxLite, constructScore,
    SIGNOFF, INTERVIEW, INTERVIEW_QUESTIONS,
    SCENARIOS, PROJECTS, HOW_TO_START, REFLECTION, TUTORIAL,
    buildSteps, answerDoc, shouldExclude, CONSENT_FORM,
} from '../participant/steps.js';


before(async () => {
    const dom = new JSDOM('<!doctype html><body></body>', { pretendToBeVisual: true });
    global.window = dom.window;
    global.document = dom.window.document;
    Object.defineProperty(global, 'navigator', {
        value: dom.window.navigator, configurable: true, writable: true,
    });
});

// ── the order of the session ─────────────────────────────────────────────────

test('nobody meets the same project twice', () => {
    for (const order of ['codoc-first', 'baseline-first']) {
        const projects = buildSteps(order).filter((s) => s.kind === 'task').map((s) => s.project);
        assert.equal(projects.length, 2);
        assert.notEqual(projects[0], projects[1]);
    }
});

test('the two conditions come in the order the participant was assigned', () => {
    const a = buildSteps('codoc-first').filter((s) => s.kind === 'task').map((s) => s.condition);
    const b = buildSteps('baseline-first').filter((s) => s.kind === 'task').map((s) => s.condition);
    assert.deepEqual(a, ['codoc', 'baseline']);
    assert.deepEqual(b, ['baseline', 'codoc']);
});

test('consent comes before anything is asked or stored', () => {
    const steps = buildSteps();
    const consent = steps.findIndex((s) => s.kind === 'consent');
    const firstAsked = steps.findIndex((s) => answerDoc(s) !== null);
    assert.ok(consent >= 0);
    assert.ok(consent < firstAsked, 'nothing is collected before consent is given');
});

test('the questionnaire comes after each task, and the scenarios only at the end', () => {
    const kinds = buildSteps().map((s) => s.kind);
    assert.equal(kinds.indexOf('questionnaire') > kinds.indexOf('task'), true);
    assert.equal(kinds.lastIndexOf('scenarios') > kinds.lastIndexOf('task'), true,
        'which one they prefer can only be asked once both are done');
    assert.equal(kinds.filter((k) => k === 'questionnaire').length, 2);
});

test('each condition stores its answers separately', () => {
    const steps = buildSteps('codoc-first').filter((s) => s.kind === 'questionnaire');
    const docs = steps.map(answerDoc);
    assert.deepEqual(docs, ['after-codoc', 'after-baseline']);
    assert.equal(new Set(docs).size, 2, 'one condition cannot overwrite the other');
});

// ── the questionnaire ────────────────────────────────────────────────────────

test('every reverse keyed item is one where agreeing is the bad direction', () => {
    // Getting this list wrong flips a result rather than breaking a test, so it
    // is written out rather than counted.
    const reversed = AFTER_CONDITION.filter((q) => q.reverse).map((q) => q.id);
    assert.deepEqual(reversed, ['tlxSuccess', 'ctl4', 'ctl5', 'doc2', 'rev3']);
});

test('a reverse keyed item is stored as answered, not flipped on the way in', () => {
    // Flipping here would mean the stored number no longer matches what the
    // person saw, and nobody could check the coding afterwards.
    const q = AFTER_CONDITION.find((x) => x.reverse);
    assert.ok(!('flip' in q) && !('score' in q),
        'the item carries only a marker; the flip happens once, during analysis');
    // And the flip, when it happens, is around the midpoint of that item's own
    // scale rather than a hardcoded number.
    const agree = AFTER_CONDITION.find((x) => x.reverse && x.c !== 'load');
    assert.equal(keyed(agree, 1), 7);
    assert.equal(keyed(agree, 4), 4);
    assert.equal(keyed({ ...agree, reverse: false }, 1), 1);
    assert.equal(keyed(agree, null), null, 'unanswered stays unanswered');
});

test('every scale has a midpoint and labelled ends', () => {
    for (const s of [AGREE, AMOUNT, PERFORMANCE]) {
        const points = (s.max - s.min) / (s.step || 1);
        assert.equal(points % 2, 0, 'an odd number of points, so there is a middle');
        assert.ok(Number.isInteger(points), 'the ends sit on the step grid');
        assert.ok(s.lowLabel && s.highLabel);
    }
    assert.notEqual(AGREE.lowLabel, AMOUNT.lowLabel,
        'workload is not answered on an agreement scale, and must not be labelled like one');
});

test('workload is collected on TLX\'s own 21 points, not the page\'s seven', () => {
    // The finding this guards is not precision. Lee et al. show that collecting
    // TLX on five or seven points moves frustration onto the physical factor and
    // splits effort across both, so a coarse scale does not measure a blurrier
    // version of the same thing — it measures a differently shaped thing.
    for (const s of [AMOUNT, PERFORMANCE]) {
        assert.equal(s.min, 0);
        assert.equal(s.max, 100);
        assert.equal(s.step, 5, 'the original is 0–100 in fives: 21 marks, 20 intervals');
    }
    assert.equal(AGREE.max - AGREE.min, 6, 'the agreement blocks stay on seven points');
});

test('each item is answered on the scale its own words describe', () => {
    for (const q of AFTER_CONDITION) {
        const expected = q.id === 'tlxSuccess' ? PERFORMANCE : (q.c === 'load' ? AMOUNT : AGREE);
        assert.equal(scaleFor(q), expected, q.id);
    }
    // Performance is the item the literature says gets broken, and it is broken
    // by labelling. It is asked the way people read it and flipped in scoring,
    // so the words at the ends must say which end is which.
    const perf = AFTER_CONDITION.find((q) => q.id === 'tlxSuccess');
    assert.ok(perf.reverse, 'asked high-is-good, so it has to be flipped');
    assert.equal(PERFORMANCE.lowLabel, 'Failure');
    assert.equal(PERFORMANCE.highLabel, 'Perfect');
});

test('every workload item shows its subscale name and definition', () => {
    // Showing only the short question is one of the reasons the six subscales
    // correlate so much more strongly in HCI studies than in TLX's validation.
    for (const q of AFTER_CONDITION.filter((x) => x.c === 'load')) {
        assert.ok(q.title, `${q.id} has no subscale name`);
        assert.ok(q.description && q.description.length > 60,
            `${q.id} has no definition to show`);
    }
    const named = AFTER_CONDITION.filter((q) => q.title).map((q) => q.id);
    assert.equal(named.length, 6, 'the definitions belong to TLX and to nothing else');
});

test('raw TLX flips performance, and refuses an incomplete answer', () => {
    // The whole reason rtlx exists: an analysis that averages the six raw
    // numbers gets a plausible answer that is wrong in exactly one direction.
    // These are the numbers from Lee et al.'s worked example.
    const load = AFTER_CONDITION.filter((q) => q.c === 'load');
    const all = (v) => Object.fromEntries(load.map((q) => [q.id, v]));

    const easy = { ...all(0), tlxSuccess: 100 };    // nothing demanded, did perfectly
    const hard = { ...all(20), tlxSuccess: 0 };     // demanding throughout, failed

    assert.equal(rtlx(easy).overall, 0, 'a perfect result on an effortless task is no load');
    assert.equal(Math.round(rtlx(hard).overall * 10) / 10, 33.3);

    // And the failure it prevents: unflipped, both of those average to 16.7.
    const raw = (a) => load.reduce((s, q) => s + a[q.id], 0) / load.length;
    assert.equal(Math.round(raw(easy) * 10), Math.round(raw(hard) * 10),
        'raw averaging cannot tell the easiest task from a demanding one');

    assert.equal(rtlx({ ...easy, tlxEffort: undefined }), null,
        'five of six is not a raw TLX');
    assert.equal(rtlx({}), null);
});

test('UMUX-Lite matches its published formula', () => {
    // (item1 + item2 − 2) × (100/12) on seven points. Written in the source as
    // a share of the scale so it survives a scale change; checked here against
    // the form it was published in.
    const published = (i1, i2) => (i1 + i2 - 2) * (100 / 12);
    for (const [i1, i2] of [[1, 1], [7, 7], [4, 4], [2, 6], [7, 1]]) {
        assert.ok(Math.abs(umuxLite({ umux1: i1, umux2: i2 }) - published(i1, i2)) < 1e-9,
            `${i1},${i2}`);
    }
    assert.equal(umuxLite({ umux1: 5 }), null, 'one of two items is not the instrument');
});

test('a construct score keys its reverse items', () => {
    // The same trap as TLX performance, in the blocks that are ours. ctl4 and
    // ctl5 are reverse keyed, so agreeing with them is the bad direction.
    const answers = { ctl1: 7, ctl2: 7, ctl3: 7, ctl4: 1, ctl5: 1 };
    assert.equal(constructScore(answers, 'control'), 7,
        'a consistent best-case answer scores at the top, not in the middle');
    assert.equal(constructScore({}, 'control'), null);
});

test('normalized puts every scale on one 0-100 range', () => {
    const mental = AFTER_CONDITION.find((q) => q.id === 'tlxMental');
    const umux = AFTER_CONDITION.find((q) => q.id === 'umux1');
    assert.equal(normalized(mental, 0), 0);
    assert.equal(normalized(mental, 100), 100);
    assert.equal(normalized(umux, 1), 0);
    assert.equal(normalized(umux, 7), 100);
    assert.equal(normalized(umux, 4), 50);
});

test('the items are asked in a fixed order with no duplicates', () => {
    assert.equal(new Set(AFTER_CONDITION.map((q) => q.id)).size, AFTER_CONDITION.length);
    assert.equal(new Set(AFTER_CONDITION.map((q) => q.text)).size, AFTER_CONDITION.length,
        'two items with the same wording would be averaged as if they were two measurements');
});

test('every item belongs to a named construct, and every construct has items', () => {
    // An item in no construct is an item nobody decided the purpose of, and it
    // would be silently dropped from every figure.
    const ids = new Set(CONSTRUCTS.map((c) => c.id));
    for (const q of AFTER_CONDITION) assert.ok(ids.has(q.c), `${q.id} is in no construct`);
    for (const c of CONSTRUCTS) {
        assert.ok(AFTER_CONDITION.some((q) => q.c === c.id), `${c.id} has no items`);
    }
});

test('the published instruments are reproduced whole', () => {
    // Their value is that a reviewer already knows what the numbers mean, and a
    // shortened one forfeits that. UMUX-Lite is two items; raw TLX is six.
    assert.equal(AFTER_CONDITION.filter((q) => q.c === 'umux').length, 2);
    assert.equal(AFTER_CONDITION.filter((q) => q.c === 'load').length, 6);
});

test('each research question has items that can answer it', () => {
    // Two questions, not three. RQ1 is understanding and RQ2 is authored
    // modification (docs/plans/2026-08-16-001-task-redesign.md); the three-RQ set
    // these blocks used to be tagged against was abandoned after the first pilot,
    // and the tags outlived it — so a block tagged RQ1 meant co-authorship here
    // and understanding in the guide, in files that get read together.
    for (const rq of ['RQ1', 'RQ2']) {
        const cs = CONSTRUCTS.filter((c) => c.rq === rq).map((c) => c.id);
        assert.ok(cs.length, `nothing measures ${rq}`);
        assert.ok(AFTER_CONDITION.some((q) => cs.includes(q.c)), `no items for ${rq}`);
    }
    assert.ok(!CONSTRUCTS.some((c) => c.rq === 'RQ3'),
        'RQ3 belongs to the abandoned design; a block still tagged with it would be reported under a question nobody is asking');
});

// ── the briefing that used to be read off a call ─────────────────────────────

test('each project explains itself on the page', () => {
    for (const name of ['scribe', 'tally']) {
        const p = PROJECTS[name];
        assert.ok(p.oneLine && p.why.length && p.commands.length,
            `${name} is missing part of its briefing`);
        // The worked pair is the whole explanation: you can see what the
        // program is for without reading either side closely.
        assert.ok(p.worked.input && p.worked.output
            && p.worked.input !== p.worked.output);
        // Four things it does, and what it does not do, so nobody goes looking
        // for a feature that is not there. Six, with a second sentence each,
        // was more than five minutes buys.
        assert.equal(p.rules.length, 4);
        assert.ok(p.limits);
        // And no file names. The briefing used to list nine source files, which
        // is a level of detail nobody can hold and nobody needs in order to
        // read a change.
        const said = [p.oneLine, ...p.why, ...p.rules.map((r) => r.what), p.limits].join(' ');
        assert.ok(!/\.py\b/.test(said), `${name}'s briefing still names source files`);
    }
});

test('the task shows the case that makes the request worth making', () => {
    // The request used to arrive with no occasion: a card saying "a config file,
    // a short report next to the output", which a person meeting the project
    // ten minutes ago cannot read. What a config file is for is the thing the
    // program currently gets wrong, so that is shown first.
    for (const name of ['scribe', 'tally']) {
        const f = PROJECTS[name].failure;
        assert.ok(f.lead && f.input && f.output && f.caption,
            `${name} asks for a change without showing why`);
        assert.ok(PROJECTS[name].ask.length >= 2,
            `${name} does not say what is being asked for`);
    }
});

test('both conditions are started from written steps, not from memory', () => {
    for (const c of ['codoc', 'baseline']) {
        const how = HOW_TO_START[c];
        assert.ok(how.steps.length);
        assert.match(how.folder('scribe'), /codoc-study/);
    }
    // The two used to differ in shape as well as content: three terminals and a
    // daemon in one arm, one terminal in the other. That is a difference in how
    // much setting up a person does, on top of the one being studied.
    assert.equal(HOW_TO_START.codoc.steps.length,
        HOW_TO_START.baseline.steps.length,
        'one arm is set up in more steps than the other');
});

// ── screening ────────────────────────────────────────────────────────────────

test('somebody who never reads a diff is flagged for exclusion', () => {
    assert.equal(shouldExclude({ readsDiff: 'Never' }), true);
    assert.equal(shouldExclude({ readsDiff: 'Rarely' }), false);
    assert.equal(shouldExclude({}), false);
});

test('the screening question does not tell them which answer excludes', () => {
    const q = PRESTUDY.find((x) => x.id === 'readsDiff');
    assert.ok(!/exclud|disqualif|must/i.test(q.label),
        'saying so would stop the answer being honest');
});

// ── the request the participant sends ────────────────────────────────────────

test('the request is copyable, and is the one the recording was made from', async () => {
    // It used to be a picture, on purpose: the participant wrote their own
    // instructions and those instructions were a measure, so a card they could
    // paste would have measured our wording instead of theirs. The task is a
    // review now. The request is a stimulus, every participant has to send the
    // same one, and a retyped paragraph is a different one.
    const { readFile } = await import('node:fs/promises');
    const root = new URL('../../docs/study-materials/replay/requests/', import.meta.url);
    for (const name of ['scribe', 'tally']) {
        const recorded = (await readFile(new URL(`${name}.txt`, root), 'utf8'))
            .replace(/\s+/g, ' ').trim();
        assert.equal(PROJECTS[name].prompt, recorded,
            `${name}'s request is not the one the change was recorded from`);
        assert.ok(!PROJECTS[name].prompt.includes('\n'),
            'a request with a line break in it submits halfway through when pasted');
    }
});

test('the task page leaves the open decisions open', () => {
    for (const name of ['scribe', 'tally']) {
        const p = PROJECTS[name];
        const said = [...p.ask, p.prompt, p.failure.lead, p.failure.caption]
            .join(' ').toLowerCase();
        // Whether the participant finds a planted problem is the measure. The
        // words below are either a policy a planted problem changes or the name
        // of the rule it changed it in, so naming one hands over a detection.
        for (const giveaway of ['indent', 'hyphen', 'page break', 'duplicate',
            'transfer', 'reference', 'furniture', 'footnote', 'rounding', 'posted']) {
            assert.ok(!said.includes(giveaway),
                `the ${name} task mentions "${giveaway}", which is one of its open decisions`);
        }
        // And it never says the change is wrong. It has not happened yet.
        for (const tell of ['wrong', 'mistake', 'bug', 'check that', 'make sure']) {
            assert.ok(!said.includes(tell), `the ${name} task primes with "${tell}"`);
        }
    }
});

test('both conditions are taught, at the same length', () => {
    // A tutorial in one arm and four sentences in the other is a difference in
    // how much the page teaches, on top of the difference the study is about.
    const codoc = TUTORIAL.codoc;
    const baseline = TUTORIAL.baseline;
    assert.equal(codoc.steps.length, baseline.steps.length,
        'one arm walks through more steps than the other');
    assert.equal(codoc.parts.length, baseline.parts.length);
    for (const g of [codoc, baseline]) {
        for (const step of g.steps) {
            assert.ok(step.points.length >= 2, `${step.title} says almost nothing`);
            assert.ok(step.figure && (step.figure.src || step.figure.todo),
                `${step.title} has no figure and no note saying what belongs there`);
        }
    }
});

// ── consent ──────────────────────────────────────────────────────────────────

test('consent points at the Google form and not at our own database', () => {
    assert.match(CONSENT_FORM, /^https:\/\/docs\.google\.com\/forms\//);
    assert.ok(CONSENT_FORM.includes('embedded=true'));
});

test('no question anywhere asks for a name or an email', () => {
    // Those live in the consent form. The rules would refuse them anyway, but a
    // question that cannot be saved is a worse failure than one never asked.
    const all = [...PRESTUDY, ...SCENARIOS.map((s) => ({ label: s.text })),
        ...AFTER_CONDITION.map((q) => ({ label: q.text }))];
    for (const q of all) {
        assert.ok(!/\b(name|e-?mail|address|phone)\b/i.test(q.label || ''),
            `"${q.label}" asks for something identifying`);
    }
});

test('everything except consent is asked in one place', () => {
    // It used to be split, which made the page explain its own plumbing: "this
    // one is here rather than in the form above because the researcher needs to
    // see it". Consent stays in Google because that is where a signature
    // belongs; nothing else does.
    assert.match(CONSENT_FORM, /^https:\/\/docs\.google\.com\/forms\//);
    assert.ok(PRESTUDY.length >= 8, 'the whole questionnaire is here');
    assert.ok(PRESTUDY.some((q) => q.id === 'readsDiff'), 'including the screening item');
});

test('the two questions that could identify somebody can be declined', () => {
    // A paper has to describe who took part, which is why they are asked at all.
    // Neither is required, and gender offers a way out that is not a refusal.
    const gender = PRESTUDY.find((q) => q.id === 'gender');
    assert.ok(gender.options.includes('Prefer not to say'));
    assert.ok(!REQUIRED.includes('age'), 'age is optional');
});

test('a follow-up only appears once it is called for', () => {
    // Nobody is asked to describe something they did not choose.
    const self = PRESTUDY.find((q) => q.id === 'genderSelf');
    assert.deepEqual(self.showWhen, { gender: 'Prefer to self-describe' });
    assert.ok(!REQUIRED.includes('genderSelf'), 'and it is never required');
});

test('the session leaves nowhere for the researcher to improvise', () => {
    // Every step either shows something written down or collects something. A
    // step whose content lives on the call is a step that differs per session.
    const kinds = new Set(buildSteps('codoc-first').map((s) => s.kind));
    for (const needed of ['intro', 'about', 'system', 'task', 'questionnaire',
        'interview', 'prestudy', 'signoff', 'reflect']) {
        assert.ok(kinds.has(needed), `no ${needed} step`);
    }
});

// ── the sign-off, and the interview ──────────────────────────────────────────

test('the sign-off is answered by the participant, not transcribed', () => {
    // It used to be typed into the dashboard while they spoke, which made it a
    // record of how well somebody explained themselves and how fast the
    // researcher could type.
    const steps = buildSteps('codoc-first').filter((s) => s.kind === 'signoff');
    assert.equal(steps.length, 2, 'once per condition');
    assert.deepEqual(steps.map(answerDoc), ['signoff-codoc', 'signoff-baseline'],
        'stored per condition, so one cannot overwrite the other');
});

test('the sign-off comes straight after the task, before anything else', () => {
    // Confidence decays the moment somebody starts answering other questions
    // about what they just did.
    const kinds = buildSteps('codoc-first').map((s) => s.kind);
    assert.equal(kinds[kinds.indexOf('task') + 1], 'signoff');
});

test('what the answer rests on takes more than one', () => {
    // "I ran the tests" and "the agent said so" are different answers, and
    // somebody who did both should be able to say so.
    const grounds = SIGNOFF.find((q) => q.id === 'grounds');
    assert.equal(grounds.type, 'multi');
    assert.ok(grounds.options.some((o) => /agent said/i.test(o)));
    assert.ok(grounds.options.some((o) => /read the description/i.test(o)));
});

test('every interview question is written down, in three parts', () => {
    // Written down so it is asked the same way every time. The researcher still
    // follows up live; the openings are fixed.
    assert.equal(INTERVIEW.length, 3);
    assert.ok(INTERVIEW_QUESTIONS.length >= 10);
    for (const q of INTERVIEW_QUESTIONS) {
        assert.ok(q.label.trim().endsWith('?'), `"${q.label}" is not a question`);
        assert.ok(q.part, `${q.id} belongs to no part`);
    }
});

test('the comparison and trust questions each name a research question', () => {
    // The adoption ones deliberately do not: they are for the discussion, not
    // for a result, and pretending otherwise would put them in a figure.
    for (const part of ['comparison', 'trust']) {
        const qs = INTERVIEW_QUESTIONS.filter((q) => q.part === part);
        assert.ok(qs.length);
        for (const q of qs) assert.ok(q.rq, `${q.id} answers no research question`);
    }
    assert.ok(INTERVIEW_QUESTIONS.filter((q) => q.part === 'adoption').every((q) => !q.rq));
});

test('no interview question tells them which way to answer', () => {
    for (const q of INTERVIEW_QUESTIONS) {
        assert.ok(!/\bbetter\b|\beasier for you\b|\bimproved\b/i.test(q.label),
            `"${q.label}" leads`);
    }
});

// ── filling a step in, for a pilot ───────────────────────────────────────────

test('every step that stores answers has defaults', async () => {
    // Otherwise a pilot presses skip on a step, nothing is filled, the Continue
    // button stays disabled, and the control looks broken rather than absent.
    const { defaultsFor } = await import('../participant/autofill.js');
    for (const step of buildSteps('codoc-first')) {
        if (answerDoc(step) === null) continue;
        const values = defaultsFor(step);
        assert.ok(values, `no defaults for ${step.kind}`);
        assert.equal(values.autofilled, true, `${step.kind} is not marked`);
    }
});

test('the defaults answer every required question', async () => {
    const { defaultsFor } = await import('../participant/autofill.js');
    const prestudy = defaultsFor({ kind: 'prestudy' });
    for (const id of REQUIRED) {
        assert.ok(prestudy[id] !== undefined && String(prestudy[id]).trim() !== '',
            `${id} was left blank`);
    }
});

test('a filled-in pilot is not screened out of its own pilot run', async () => {
    // The screening question's excluding answer is a real option, and taking
    // options in order would have picked it.
    const { defaultsFor } = await import('../participant/autofill.js');
    assert.equal(shouldExclude(defaultsFor({ kind: 'prestudy' })), false);
});

test('the defaults come from the instrument, not from a second copy of it', async () => {
    // A question added to the instrument has to be filled by this without
    // anybody remembering to come here, or the skip quietly stops working on
    // whichever step gained the question.
    const { defaultsFor } = await import('../participant/autofill.js');
    const after = defaultsFor({ kind: 'questionnaire' });
    for (const q of AFTER_CONDITION) {
        assert.ok(after[q.id] !== undefined, `${q.id} was not filled`);
        const s = scaleFor(q);
        assert.ok(after[q.id] > s.min && after[q.id] < s.max,
            'a default sits in the middle rather than on an endpoint');
    }
    const signoff = defaultsFor({ kind: 'signoff' });
    for (const q of SIGNOFF) assert.ok(signoff[q.id] !== undefined, `${q.id} was not filled`);
});

test('the interview is spoken, so the page neither shows it nor stores it', async () => {
    // It was typed on the participant's page, which got short written answers to
    // questions whose value is the follow-up, at the end of two hours. The
    // researcher asks them on the call and types what was said into the
    // dashboard, so this page has nothing to fill in and nothing to save.
    const { defaultsFor } = await import('../participant/autofill.js');
    const step = buildSteps().find((s) => s.kind === 'interview');
    assert.ok(step, 'the step is still in the session, as a hand-off');
    assert.equal(answerDoc(step), null, 'but it stores nothing');
    assert.equal(defaultsFor(step), null, 'so there is nothing to autofill');

    // The questions themselves stay, because the dashboard asks from them.
    assert.ok(INTERVIEW_QUESTIONS.length >= 10, 'and they are still written down');
});

test('nothing outside a pilot code can reach the skip', async () => {
    const { isPilotCode } = await import('../shared/schema.js');
    assert.equal(isPilotCode('p-abcdefghjkmn'), false);
    assert.equal(isPilotCode('pilot-abcdefghjkmn'), true);
    assert.equal(isPilotCode(''), false);
    assert.equal(isPilotCode(null), false);
});

// ── the page and the setup script, against each other ────────────────────────

test('the page names the launcher that setup actually writes', async () => {
    // The page told them to run `claude`. Setup writes `./claude-study`, which
    // is the whole isolation: plain claude picks up their own login and bills
    // their own plan, and nothing on either side would have said so. Two files,
    // each correct, disagreeing.
    const { readFileSync } = await import('node:fs');
    const { join, dirname } = await import('node:path');
    const { fileURLToPath } = await import('node:url');
    const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
    const setup = readFileSync(
        join(root, 'docs', 'study-materials', 'scripts', 'setup.sh'), 'utf8');

    const named = Object.values(HOW_TO_START)
        .flatMap((h) => h.steps.map(([, cmd]) => cmd).filter(Boolean));
    assert.ok(!named.some((c) => /^claude(\s|$)/.test(c)),
        'nothing tells them to run plain claude, which would use their own account');
    assert.match(setup, /claude-study/, 'and setup writes that launcher');

    // The agent is started on the task page, at the moment the request is sent,
    // because that is the only place a participant needs it. It is still the
    // launcher, in both arms.
    const page = readFileSync(join(root, 'study-app', 'participant', 'app.js'), 'utf8');
    assert.match(page, /cmd\('\.\/claude-study'\)/,
        'the task page starts the agent through the study launcher');

    // Nothing tells them to start the daemon by hand any more. It used to be a
    // terminal of its own in one arm and nothing in the other, which is a
    // difference in how much setting up a person does on top of the one the
    // study is about.
    assert.ok(!named.some((c) => /codoc watch/.test(c)),
        'a participant is still starting the daemon by hand');
    assert.match(setup, /--codoc-bin/,
        'and nothing starts it for them once the recording has played');
});

// ── copying a command ────────────────────────────────────────────────────────

test('a command renders with a copy button carrying the exact text', async () => {
    const { cmd } = await import('../participant/copy.js');
    const html = cmd('./setup.sh p-abcdefghjkmn codoc-first');
    assert.match(html, /class="pick"/, 'the command is still shown');
    assert.match(html, /button[^>]*class="copy"/, 'and has a copy button');
    assert.match(html, /data-copy="\.\/setup\.sh p-abcdefghjkmn codoc-first"/,
        'the button carries the command verbatim, not a re-typed copy of it');
});

test('a command with markup in it cannot inject anything', async () => {
    const { cmd } = await import('../participant/copy.js');
    // The participant code comes off the query string and is interpolated into
    // this, so it reaches the page unfiltered. Parse the result and check the
    // DOM rather than the string: the payload's own characters survive as text,
    // and only whether they became markup matters.
    const html = cmd('./setup.sh "><img src=x onerror=alert(1)>');
    const host = document.createElement('div');
    host.innerHTML = html;

    assert.equal(host.querySelectorAll('img').length, 0, 'no tag was created');
    assert.equal(host.children.length, 1, 'and nothing escaped the wrapper');
    const button = host.querySelector('button.copy');
    assert.equal(button.dataset.copy, './setup.sh "><img src=x onerror=alert(1)>',
        'the command still round-trips exactly, quotes and all');
    assert.equal(host.querySelector('code.pick').textContent,
        './setup.sh "><img src=x onerror=alert(1)>');
});

test('clicking copy writes the command to the clipboard', async () => {
    const { cmd, wireCopy } = await import('../participant/copy.js');
    const root = document.createElement('div');
    root.innerHTML = cmd('./collect.sh p-abcdefghjkmn');
    document.body.append(root);

    let written = null;
    Object.defineProperty(global.navigator, 'clipboard', {
        value: { writeText: async (t) => { written = t; } },
        configurable: true,
    });
    wireCopy(root);
    const button = root.querySelector('button.copy');
    button.click();
    await new Promise((r) => setTimeout(r, 0));

    assert.equal(written, './collect.sh p-abcdefghjkmn');
    assert.equal(button.textContent, 'Copied', 'and it says so');
    root.remove();
});

test('a refused clipboard tells them what to press instead', async () => {
    const { cmd, wireCopy } = await import('../participant/copy.js');
    const root = document.createElement('div');
    root.innerHTML = cmd('./setup.sh --check');
    document.body.append(root);

    // Chrome can refuse the permission. A button that still says "Copied" would
    // leave somebody pasting whatever was in the clipboard before.
    Object.defineProperty(global.navigator, 'clipboard', {
        value: { writeText: async () => { throw new Error('denied'); } },
        configurable: true,
    });
    wireCopy(root);
    const button = root.querySelector('button.copy');
    button.click();
    await new Promise((r) => setTimeout(r, 0));

    assert.equal(button.textContent, 'Press Cmd+C');
    root.remove();
});

test('nothing the participant reads names the condition', async () => {
    // The folders were ~/codoc-study/scribe-baseline and ~/codoc-study/tally,
    // so half the participants spent the session typing "baseline" into a
    // terminal and then answered a questionnaire comparing the two. The word
    // ranks the arms, and it was on screen throughout one of them.
    //
    // The manipulation is not a secret: one folder holds a feature tree and the
    // other a CLAUDE.md, and they will see that. It just does not get a name
    // that says which one is the control.
    for (const [name, how] of Object.entries(HOW_TO_START)) {
        for (const project of ['scribe', 'tally']) {
            const folder = how.folder(project);
            assert.equal(folder, `~/codoc-study/${project}`,
                `${name} points at a folder named for the project alone`);
        }
        for (const [text, command] of how.steps) {
            const shown = `${text} ${command || ''}`;
            assert.ok(!/baseline/i.test(shown),
                `a step in ${name} says "baseline" to the participant: ${shown}`);
        }
        assert.ok(!/baseline/i.test(how.title),
            `${name} describes itself without ranking the two`);
    }

    // And the setup command the page prints unpacks into those same folders.
    const { readFileSync } = await import('node:fs');
    const { join, dirname } = await import('node:path');
    const { fileURLToPath } = await import('node:url');
    const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
    const setup = readFileSync(
        join(root, 'docs', 'study-materials', 'scripts', 'setup.sh'), 'utf8');
    assert.match(setup, /--strip-components=1/,
        'setup unpacks scribe-baseline.tar.gz into a folder called scribe');
    assert.match(setup, /PROJECTS="scribe tally"/);
});

test('the preference item asks about kinds of work, and can go against the tool', async () => {
    // The earlier list mixed how big a job is, how long you own the code, and how
    // much of a hurry you are in, inside single items like "a throwaway script
    // you will delete tomorrow". An answer to that cannot be read as being about
    // any one of them.
    assert.ok(SCENARIOS.length >= 6, 'enough activities to tell them apart');
    for (const s of SCENARIOS) {
        assert.ok(s.text.split(/\s+/).length >= 5,
            `"${s.text}" is too terse to reason about`);
        assert.ok(!/\bcodoc\b/i.test(s.text), 'no item names the tool');
    }

    // The load-bearing property. Three items are cases where a written
    // description plausibly does NOT help: a fault you can already reproduce is
    // answered by running the code, a file that does not exist yet has nothing
    // to describe, and an hour-long fix is where keeping a description current is
    // pure overhead. A list the tool could only win on measures the list.
    const against = SCENARIOS.filter((s) =>
        /reproduce/i.test(s.text)            // debugging
        || /does not exist yet/i.test(s.text) // greenfield
        || /within the hour/i.test(s.text));  // time pressure
    assert.ok(against.length >= 3,
        'at least three items where a description plausibly does not help');
});

// ── after the task, closed book ──────────────────────────────────────────────

test('the only question round is the one about their own change', async () => {
    // The open-book round that used to sit before the task asked about the
    // CODEBASE. The task is now a review of a change to that codebase, and its
    // first half is spent working the same thing out, so the round was the first
    // half of the task with the clock running twice. What the study is about is
    // whether the person still owns the change, and that can only be asked
    // about their own change.
    const steps = buildSteps('codoc-first');
    assert.equal(steps.filter((s) => s.kind === 'quiz').length, 0,
        'the pre-task question round is still in the session');

    const reflect = steps.filter((s) => s.kind === 'reflect');
    assert.equal(reflect.length, 2, 'one reflection per condition');
    for (const step of reflect) {
        const kinds = steps.map((s) => s.kind);
        const task = steps.findIndex((s) => s.kind === 'task' && s.n === step.n);
        assert.ok(steps.indexOf(step) > task, 'it comes after the task it is about');
        assert.equal(kinds.filter((k) => k === 'reflect').length, 2);
    }

    // Stored per condition, so one cannot overwrite the other.
    const docs = reflect.map(answerDoc);
    assert.deepEqual(docs, ['reflect-codoc', 'reflect-baseline']);
    assert.equal(new Set(docs).size, 2);
});

test('the after-task questions are about their own change and have right answers', async () => {
    // They were four boxes to type in. Freeform got short answers to questions
    // whose value is in the follow-up, at the end of two hours, and nothing
    // comparable between participants. These are multiple choice with a key, so
    // two sessions can be compared, and the follow-up happens out loud in the
    // closing interview instead.
    const { AFTER_QUIZZES } = await import('../participant/quiz.js');
    for (const [project, questions] of Object.entries(AFTER_QUIZZES)) {
        assert.equal(questions.length, 5, `${project} asks five`);
        for (const q of questions) {
            assert.equal(q.options.length, 4, `${project} Q${q.n} offers four`);
            // The key must NOT be here: this file ships to a browser.
            assert.ok(!('answer' in q), `${project} Q${q.n} carries its answer to the page`);
        }
        // Every one names the participant's own change or the codebase it landed
        // in. A question answerable from the briefing measures reading.
        const text = questions.map((q) => q.question).join(' ').toLowerCase();
        assert.match(text, /your change|you had|you chose|you decided|picks this up/,
            `${project}'s set does not refer to what they did`);
    }

    // And the answers live only where the dashboard can see them.
    const { afterFor } = await import('../experimenter/forms.js');
    for (const project of ['scribe', 'tally']) {
        const set = afterFor(project);
        assert.equal(set.length, 5);
        for (const q of set) {
            assert.ok(q.answer, `${project} Q${q.n} has no answer to mark against`);
            assert.ok(q.options.some((o) => o.letter === q.answer),
                `${project} Q${q.n} marks an option that does not exist`);
        }
    }
});

test('the two projects ask the same shape after the task', async () => {
    // Each participant does one project each way, so a harder set on one project
    // lands entirely on whichever condition drew it.
    const { AFTER_QUIZZES } = await import('../participant/quiz.js');
    const shape = (qs) => qs.map((q) => q.band).sort().join(',');
    assert.equal(shape(AFTER_QUIZZES.scribe), shape(AFTER_QUIZZES.tally),
        'scribe and tally cover the same bands, band for band');
});

test('a reflection can be finished without writing an essay', async () => {
    // The scale is required; the four written answers are not. A required box
    // buys blank characters typed to get past the button, and "I am not sure" is
    // a finding rather than a gap.
    const { defaultsFor } = await import('../participant/autofill.js');
    const step = buildSteps().find((s) => s.kind === 'reflect');
    const filled = defaultsFor(step);
    for (const q of REFLECTION) {
        assert.ok(filled[q.id] !== undefined, `${q.id} was not filled`);
    }
});

