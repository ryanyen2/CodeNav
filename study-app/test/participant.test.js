// The participant flow: what gets asked, in what order, and what is stored.
//
//   node --test test/participant.test.js
import test, { before } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import {
    PRESTUDY, REQUIRED, AFTER_CONDITION, CONSTRUCTS, AGREE, AMOUNT, scaleFor, keyed,
    SIGNOFF, INTERVIEW, INTERVIEW_QUESTIONS,
    SCENARIOS, TASK_CARDS, PROJECTS, HOW_TO_START,
    buildSteps, answerDoc, shouldExclude, CONSENT_FORM,
} from '../participant/steps.js';

let drawCard;

before(async () => {
    const dom = new JSDOM('<!doctype html><body></body>', { pretendToBeVisual: true });
    global.window = dom.window;
    global.document = dom.window.document;
    Object.defineProperty(global, 'navigator', {
        value: dom.window.navigator, configurable: true, writable: true,
    });
    ({ drawCard } = await import('../participant/card.js'));
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
    // scale rather than a hardcoded 8.
    assert.equal(keyed({ ...q, reverse: true }, 1), 7);
    assert.equal(keyed({ ...q, reverse: true }, 4), 4);
    assert.equal(keyed({ ...q, reverse: false }, 1), 1);
    assert.equal(keyed(q, null), null, 'unanswered stays unanswered');
});

test('both scales have a midpoint and labelled ends', () => {
    for (const s of [AGREE, AMOUNT]) {
        assert.equal((s.max - s.min) % 2, 0, 'an odd number of points, so there is a middle');
        assert.ok(s.lowLabel && s.highLabel);
    }
    assert.notEqual(AGREE.lowLabel, AMOUNT.lowLabel,
        'workload is not answered on an agreement scale, and must not be labelled like one');
});

test('the workload block runs low to high, and the rest disagree to agree', () => {
    for (const q of AFTER_CONDITION) {
        assert.equal(scaleFor(q), q.c === 'load' ? AMOUNT : AGREE, q.id);
    }
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
    for (const rq of ['RQ1', 'RQ2', 'RQ3']) {
        const cs = CONSTRUCTS.filter((c) => c.rq === rq).map((c) => c.id);
        assert.ok(cs.length, `nothing measures ${rq}`);
        assert.ok(AFTER_CONDITION.some((q) => cs.includes(q.c)), `no items for ${rq}`);
    }
});

// ── the briefing that used to be read off a call ─────────────────────────────

test('each project explains itself on the page', () => {
    for (const name of ['scribe', 'tally']) {
        const p = PROJECTS[name];
        assert.ok(p.oneLine && p.problem.length && p.commands.length && p.layout.length,
            `${name} is missing part of its briefing`);
        // The before and after. The pair is the whole explanation: you can see
        // what the program is for without reading either one closely.
        assert.ok(p.before && p.after && p.before !== p.after);
        // Six things it does, and what it does not do, so nobody goes looking
        // for a feature that is not there.
        assert.equal(p.does.length, 6);
        assert.ok(p.notScope);
    }
});

test('each briefing says the rules could have gone another way', () => {
    // This is the study's premise. Without it a participant reads the code as
    // the only possible version of itself, and the description has nothing to
    // add that the code does not already say.
    for (const name of ['scribe', 'tally']) {
        const p = PROJECTS[name];
        assert.ok(p.judgement.length >= 2, `${name} names no judgement calls`);
        for (const [what, why] of p.judgement) {
            assert.ok(what && why.length > 40, `${name}: "${what}" is not explained`);
        }
    }
});

test('both conditions are started from written steps, not from memory', () => {
    for (const c of ['codoc', 'baseline']) {
        const how = HOW_TO_START[c];
        assert.ok(how.steps.length && how.about.length);
        assert.match(how.folder('scribe'), /codoc-study/);
    }
    // The difference between them IS the manipulation, so it must not be
    // improvised differently for each participant.
    assert.notDeepEqual(HOW_TO_START.codoc.about, HOW_TO_START.baseline.about);
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

// ── the task card ────────────────────────────────────────────────────────────

test('the card is drawn as a picture with no text to select', () => {
    // The guide has always said to show the card as an image. If it can be
    // selected it can be pasted into the agent, and then the agent is working
    // from our wording instead of the participant's, which is one of the things
    // the study measures.
    const el = document.createElement('div');
    document.body.append(el);
    drawCard(el, TASK_CARDS.scribe, { width: 700 });

    const canvas = el.querySelector('canvas');
    if (canvas) {
        assert.equal(el.textContent.trim(), '', 'there is no text in the page to select');
        assert.match(canvas.getAttribute('aria-label'), /Task card/);
        assert.ok(!canvas.getAttribute('aria-label').includes('must not appear'),
            'and the alternative text names the card without repeating it');
    } else {
        // No drawing surface here, which is the fallback path. A card that cannot
        // be seen would be worse than one that can be selected, so it degrades to
        // text rather than to nothing.
        const fallback = el.querySelector('.card-fallback');
        assert.ok(fallback, 'it falls back to something visible');
        assert.ok(fallback.textContent.includes(TASK_CARDS.scribe.title));
    }
});

test('both cards leave the open decisions open', () => {
    for (const [name, card] of Object.entries(TASK_CARDS)) {
        const text = [card.title, ...card.lines].join(' ').toLowerCase();
        assert.ok(text.includes('decide anything'), `${name} invites them to decide`);
        // The card must not settle any of the four open decisions, because
        // whether the participant settles them is the measure.
        for (const giveaway of ['indent', 'hyphen', 'paragraph', 'page break',
            'duplicate', 'transfer', 'uncategorised', 'reference']) {
            assert.ok(!text.includes(giveaway),
                `the ${name} card mentions "${giveaway}", which is one of its open decisions`);
        }
    }
    // And the card is short. A long one starts answering the questions it is
    // supposed to leave open.
    for (const name of ['scribe', 'tally']) {
        assert.ok(TASK_CARDS[name].lines.filter((l) => l.trim()).length <= 8,
            `${name}'s card is too long to have left anything open`);
    }
});

test('drawing twice reuses the canvas rather than stacking them', () => {
    const el = document.createElement('div');
    document.body.append(el);
    drawCard(el, TASK_CARDS.tally, { width: 700 });
    drawCard(el, TASK_CARDS.tally, { width: 700 });
    assert.ok(el.querySelectorAll('canvas').length <= 1, 'no stacking');
    assert.ok(el.querySelectorAll('.card-fallback').length <= 1);
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
    for (const needed of ['intro', 'about', 'task', 'questionnaire', 'interview',
        'prestudy', 'signoff', 'quiz']) {
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
