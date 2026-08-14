// The participant flow: what gets asked, in what order, and what is stored.
//
//   node --test test/participant.test.js
import test, { before } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import {
    BACKGROUND, AFTER_CONDITION, SCALE, SCENARIOS, TASK_CARDS,
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

test('the reverse keyed items are exactly the five the design marks', () => {
    // The design marks five items (R), of which four are called the honesty
    // valves. Those are different lists and conflating them changes the
    // instrument.
    const reversed = AFTER_CONDITION.filter((q) => q.reverse).map((q) => q.id);
    assert.deepEqual(reversed, ['q3', 'q5', 'q7', 'q9', 'q11']);
    const valves = ['q3', 'q5', 'q9', 'q11'];
    assert.ok(valves.every((v) => reversed.includes(v)),
        'the valves are among the reverse keyed items');
});

test('a reverse keyed item is stored as answered, not flipped on the way in', () => {
    // Flipping here would mean the stored number no longer matches what the
    // person saw, and nobody could check the coding afterwards.
    const q = AFTER_CONDITION.find((x) => x.reverse);
    assert.ok(q, 'there is at least one');
    assert.ok(!('flip' in q) && !('score' in q),
        'the item carries only a marker; the flip happens once, during analysis');
});

test('the scale has a midpoint and both ends are labelled', () => {
    assert.equal(SCALE.min, 1);
    assert.equal(SCALE.max, 7);
    assert.equal((SCALE.max - SCALE.min) % 2, 0, 'an odd number of points, so there is a middle');
    assert.ok(SCALE.lowLabel && SCALE.highLabel);
});

test('the twelve items are asked in a fixed order with no duplicates', () => {
    assert.equal(AFTER_CONDITION.length, 12);
    assert.equal(new Set(AFTER_CONDITION.map((q) => q.id)).size, 12);
    assert.equal(new Set(AFTER_CONDITION.map((q) => q.text)).size, 12);
});

// ── screening ────────────────────────────────────────────────────────────────

test('somebody who never reads a diff is flagged for exclusion', () => {
    assert.equal(shouldExclude({ readsDiff: 'Never' }), true);
    assert.equal(shouldExclude({ readsDiff: 'Rarely' }), false);
    assert.equal(shouldExclude({}), false);
});

test('the screening question does not tell them which answer excludes', () => {
    const q = BACKGROUND.find((x) => x.id === 'readsDiff');
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
    const all = [...BACKGROUND, ...SCENARIOS.map((s) => ({ label: s.text })),
        ...AFTER_CONDITION.map((q) => ({ label: q.text }))];
    for (const q of all) {
        assert.ok(!/\b(name|e-?mail|address|phone)\b/i.test(q.label || ''),
            `"${q.label}" asks for something identifying`);
    }
});
