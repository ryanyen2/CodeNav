// The participant flow: what gets asked, in what order, and what is stored.
//
//   node --test test/participant.test.js
import test, { before } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import {
    SCREENING, AFTER_CONDITION, CONSTRUCTS, AGREE, AMOUNT, scaleFor, keyed,
    SCENARIOS, TASK_CARDS, PROJECTS, HOW_TO_START,
    buildSteps, answerDoc, shouldExclude, CONSENT_FORM, PRESTUDY_FORM,
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
    for (const name of ['hearth', 'ember']) {
        const p = PROJECTS[name];
        assert.ok(p.what.length && p.commands.length && p.layout.length && p.words.length,
            `${name} is missing part of its briefing`);
        // The two words the task turns on. Without them the task card is a
        // vocabulary test rather than a programming one.
        assert.equal(p.words.length, 2);
    }
});

test('both conditions are started from written steps, not from memory', () => {
    for (const c of ['codoc', 'baseline']) {
        const how = HOW_TO_START[c];
        assert.ok(how.steps.length && how.about.length);
        assert.match(how.folder('hearth'), /codoc-study/);
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
    const q = SCREENING.find((x) => x.id === 'readsDiff');
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
    drawCard(el, TASK_CARDS.hearth, { width: 700 });

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
        assert.ok(fallback.textContent.includes(TASK_CARDS.hearth.title));
    }
});

test('both cards leave the open decisions open', () => {
    for (const [name, card] of Object.entries(TASK_CARDS)) {
        const text = [card.title, ...card.lines].join(' ').toLowerCase();
        assert.ok(text.includes('decide anything'), `${name} invites them to decide`);
        // The things the card must not settle, because whether the participant
        // settles them is the measure.
        assert.ok(!text.includes('signature'), `${name} does not give away the hidden rule`);
        assert.ok(!text.includes('incremental'), `${name} does not mention incremental builds`);
    }
    assert.ok(!TASK_CARDS.ember.lines.join(' ').toLowerCase().includes('notification'),
        'the ember card stays silent on notifications, which is its open decision');
});

test('drawing twice reuses the canvas rather than stacking them', () => {
    const el = document.createElement('div');
    document.body.append(el);
    drawCard(el, TASK_CARDS.ember, { width: 700 });
    drawCard(el, TASK_CARDS.ember, { width: 700 });
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
    const all = [...SCREENING, ...SCENARIOS.map((s) => ({ label: s.text })),
        ...AFTER_CONDITION.map((q) => ({ label: q.text }))];
    for (const q of all) {
        assert.ok(!/\b(name|e-?mail|address|phone)\b/i.test(q.label || ''),
            `"${q.label}" asks for something identifying`);
    }
});

test('demographics go to Google, and only the screening answer is stored here', () => {
    // Gender and age belong with consent, not beside a session log in the study
    // database. The cost is that those answers are joined by hand, keyed on the
    // code typed into the form, and that trade is deliberate.
    assert.match(PRESTUDY_FORM, /^https:\/\/docs\.google\.com\/forms\//);
    assert.match(CONSENT_FORM, /^https:\/\/docs\.google\.com\/forms\//);
    assert.notEqual(PRESTUDY_FORM, CONSENT_FORM);
    assert.equal(SCREENING.length, 1,
        'anything more asked here is something the Google form already asks');
});

test('the session leaves nowhere for the researcher to improvise', () => {
    // Every step either shows something written down or collects something. A
    // step whose content lives on the call is a step that differs per session.
    const kinds = new Set(buildSteps('codoc-first').map((s) => s.kind));
    for (const needed of ['intro', 'about', 'task', 'questionnaire', 'debrief']) {
        assert.ok(kinds.has(needed), `no ${needed} step`);
    }
});
