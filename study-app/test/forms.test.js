// The forms the researcher fills in, and the sheets they come from.
//
//   node --test test/forms.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseSheet } from '../scripts/extract-questions.mjs';
import {
    OPEN_DECISIONS, SETTLED_BY, GROUNDS, questionsFor, rounds,
    emptyAssessment, outstanding,
} from '../experimenter/forms.js';

const sheet = (p) => readFileSync(
    new URL(`../../docs/study-materials/questions-${p}.md`, import.meta.url), 'utf8');

// ── the sheets are the source ────────────────────────────────────────────────

test('the dashboard reads the same sheets the researcher reads from', () => {
    // One source of truth. A second copy in JavaScript would drift, and the
    // sheets are what gets frozen at pre-registration.
    for (const project of ['hearth', 'ember']) {
        const parsed = parseSheet(sheet(project));
        assert.equal(parsed.length, 10);
        assert.deepEqual(parsed.map((q) => q.code), questionsFor(project).map((q) => q.code));
    }
});

test('every question has all three scoring rules', () => {
    for (const project of ['hearth', 'ember']) {
        for (const q of questionsFor(project)) {
            for (const s of ['0', '1', '2']) {
                assert.ok(q.scores[s] && q.scores[s].length > 10,
                    `${project} ${q.code} has no rule for ${s}`);
            }
        }
    }
});

test('the two anchors are asked in both rounds and nothing else is', () => {
    // The change between the two answers is the measure, so the repeated ones
    // have to be exactly the ones the sheet marks.
    for (const project of ['hearth', 'ember']) {
        const r = rounds(project);
        const repeated = questionsFor(project).filter((q) => q.repeated).map((q) => q.code);
        assert.deepEqual(repeated, ['F1', 'S1']);
        assert.equal(r[1].length, 6);
        assert.equal(r[2].length, 6, 'four new ones plus the two anchors');
        assert.ok(r[2].slice(0, 2).every((q) => q.repeated));
    }
});

test('the two projects ask the same shape of question', () => {
    const a = questionsFor('hearth').map((q) => q.code);
    const b = questionsFor('ember').map((q) => q.code);
    assert.deepEqual(a, b, 'matched item for item, or the conditions are not comparable');
});

// ── what is recorded ─────────────────────────────────────────────────────────

test('closed book and open book are stored separately', () => {
    const a = emptyAssessment('hearth');
    const keys = Object.keys(a.scores);
    assert.ok(keys.some((k) => k.endsWith('-closed')));
    assert.ok(keys.some((k) => k.endsWith('-open')));
    // One overwriting the other would erase the only thing this measures.
    for (const k of keys.filter((x) => x.endsWith('-closed'))) {
        assert.ok(keys.includes(k.replace('-closed', '-open')));
    }
});

test('an anchor scored in both rounds keeps four separate numbers', () => {
    const a = emptyAssessment('hearth');
    for (const k of ['F1-r1-closed', 'F1-r1-open', 'F1-r2-closed', 'F1-r2-open']) {
        assert.ok(k in a.scores, `${k} is missing`);
    }
});

test('each task has four open decisions and three ways to settle one', () => {
    for (const project of ['hearth', 'ember']) {
        assert.equal(OPEN_DECISIONS[project].length, 4);
    }
    assert.equal(SETTLED_BY.length, 3);
    assert.ok(SETTLED_BY.some((s) => /never noticed/i.test(s)),
        'including the one that matters most');
});

test('the sign-off can rest on more than one thing', () => {
    // People run the tests and read the diff. Forcing a single answer would lose
    // that, and what it rested on is the measure rather than the number.
    assert.ok(GROUNDS.length >= 4);
    const a = emptyAssessment('hearth');
    assert.ok(Array.isArray(a.signoffGrounds));
});

test('the open decisions do not give away the hidden rule', () => {
    for (const project of ['hearth', 'ember']) {
        const text = OPEN_DECISIONS[project].join(' ').toLowerCase();
        assert.ok(!text.includes('signature'));
        assert.ok(!text.includes('stale'));
    }
});

// ── what is still missing ────────────────────────────────────────────────────

test('an empty record reports everything as outstanding', () => {
    const gaps = outstanding(emptyAssessment('hearth'), 'hearth');
    assert.ok(gaps.some((g) => /sign-off number/.test(g)));
    assert.ok(gaps.some((g) => /open decision/.test(g)));
    assert.ok(gaps.some((g) => /unscored/.test(g)));
});

test('a finished record reports nothing outstanding', () => {
    const a = emptyAssessment('hearth');
    a.signoffConfidence = 4;
    a.signoffGrounds = ['Ran the tests'];
    a.signoffVerbatim = 'I think so, the tests pass and I read most of it.';
    for (const d of OPEN_DECISIONS.hearth) a.decisions[d] = SETTLED_BY[0];
    for (const k of Object.keys(a.scores)) if (k.endsWith('-closed')) a.scores[k] = 2;
    assert.deepEqual(outstanding(a, 'hearth'), []);
});

test('a zero score counts as answered', () => {
    // Zero is a real score. Treating it as missing would quietly ask for it again.
    const a = emptyAssessment('hearth');
    a.signoffConfidence = 1;
    a.signoffGrounds = ['The agent said so'];
    a.signoffVerbatim = 'no idea really';
    for (const d of OPEN_DECISIONS.hearth) a.decisions[d] = SETTLED_BY[2];
    for (const k of Object.keys(a.scores)) if (k.endsWith('-closed')) a.scores[k] = 0;
    assert.deepEqual(outstanding(a, 'hearth'), []);
});
