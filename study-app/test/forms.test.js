// The forms the researcher fills in, and the quiz the participant answers.
//
//   node --test test/forms.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseQuiz } from '../scripts/extract-questions.mjs';
import {
    OPEN_DECISIONS, SETTLED_BY, GROUNDS, CONSISTENCY, COUPLED_DECISION,
    BANDS, SITTINGS, questionsFor, bandsFor, score,
    emptyAssessment, outstanding,
} from '../experimenter/forms.js';
import { QUIZZES } from '../participant/quiz.js';

const study = (p) => readFileSync(
    new URL(`../../docs/study-materials/projects/${p}/STUDY.md`, import.meta.url), 'utf8');

const PROJECTS = ['scribe', 'tally'];

// ── the STUDY files are the source ───────────────────────────────────────────

test('the dashboard reads the same quiz the study file holds', () => {
    // One source of truth. A second copy in JavaScript would drift, and the
    // STUDY files are what gets frozen at pre-registration.
    for (const project of PROJECTS) {
        const parsed = parseQuiz(study(project));
        assert.equal(parsed.length, 12);
        assert.deepEqual(parsed.map((q) => q.n), questionsFor(project).map((q) => q.n));
        assert.deepEqual(parsed.map((q) => q.answer), questionsFor(project).map((q) => q.answer));
    }
});

test('the participant copy carries no answers', () => {
    // It ships to a browser. A participant who opened the console would find
    // them, and the second sitting would measure their curiosity.
    const raw = readFileSync(new URL('../participant/quiz.js', import.meta.url), 'utf8');
    assert.ok(!/"answer"/.test(raw), 'the answer key is not in the participant bundle');
    for (const project of PROJECTS) {
        for (const q of QUIZZES[project]) {
            assert.equal(q.answer, undefined, `${project} Q${q.n} carries its answer`);
        }
    }
});

test('the participant sees the same questions in the same order', () => {
    for (const project of PROJECTS) {
        assert.deepEqual(
            QUIZZES[project].map((q) => q.question),
            questionsFor(project).map((q) => q.question));
    }
});

test('every question has four options and exactly one right one', () => {
    for (const project of PROJECTS) {
        for (const q of questionsFor(project)) {
            assert.equal(q.options.length, 4, `${project} Q${q.n}`);
            assert.ok(['a', 'b', 'c', 'd'].includes(q.answer), `${project} Q${q.n}`);
            assert.ok(q.options.some((o) => o.letter === q.answer));
        }
    }
});

test('the four bands RQ1 asks in are all present', () => {
    for (const project of PROJECTS) {
        const bands = bandsFor(project).map((g) => g.band);
        for (const needed of BANDS) assert.ok(bands.includes(needed), `${project} has no ${needed}`);
    }
});

test('the two projects ask the same shape of quiz', () => {
    const shape = (p) => bandsFor(p).map((g) => `${g.band}:${g.questions.length}`);
    assert.deepEqual(shape('scribe'), shape('tally'),
        'matched band for band, or the conditions are not comparable');
});

// ── what is recorded ─────────────────────────────────────────────────────────

test('the quiz is stored twice, before and after', () => {
    const a = emptyAssessment('scribe');
    assert.equal(Object.keys(a.answers).length, 12 * SITTINGS.length);
    for (const sitting of SITTINGS) assert.ok(`q1-${sitting}` in a.answers);
});

test('what they chose is stored, not whether it was right', () => {
    // A stored right-or-wrong cannot be re-marked if a question turns out to be
    // ambiguous, and it hides which wrong option attracted people.
    const a = emptyAssessment('scribe');
    a.answers['q1-before'] = 'c';
    assert.equal(score(a, 'scribe', 'before').right, questionsFor('scribe')[0].answer === 'c' ? 1 : 0);
    assert.equal(a.answers['q1-before'], 'c', 'the letter survives');
});

test('scoring counts only what was answered', () => {
    const a = emptyAssessment('tally');
    const first = questionsFor('tally')[0];
    a.answers[`q${first.n}-after`] = first.answer;
    const result = score(a, 'tally', 'after');
    assert.deepEqual([result.right, result.answered, result.of], [1, 1, 12]);
});

test('every open decision has a consistency rating beside it', () => {
    // The primary outcome. Stored apart from who settled it, because a decision
    // somebody made themselves can still contradict the codebase.
    for (const project of PROJECTS) {
        const a = emptyAssessment(project);
        assert.equal(Object.keys(a.decisions).length, 4);
        assert.deepEqual(Object.keys(a.consistency), Object.keys(a.decisions));
    }
});

test('the coupled decision is the last one in both projects', () => {
    // It is where two rules meet, and it is reached by deciding rather than by
    // tripping over it. Both task cards are built so it comes last.
    assert.equal(COUPLED_DECISION, 3);
    for (const project of PROJECTS) {
        assert.equal(OPEN_DECISIONS[project].length, 4);
        assert.ok(OPEN_DECISIONS[project][COUPLED_DECISION]);
    }
});

test('consistency is rated nought to two, and says what each means', () => {
    assert.equal(CONSISTENCY.length, 3);
    for (const label of CONSISTENCY) assert.ok(label.length > 20, label);
});

test('the ways a decision can be settled are the three that matter', () => {
    assert.equal(SETTLED_BY.length, 3);
    assert.match(SETTLED_BY[2], /never noticed/);
});

test('what the sign-off rested on is offered, not typed', () => {
    assert.ok(GROUNDS.includes('Read the description'));
    assert.ok(GROUNDS.includes('The agent said so'));
});

test('gaps are named while the call is still on', () => {
    const gaps = outstanding(emptyAssessment('scribe'), 'scribe');
    assert.ok(gaps.length, 'a blank record has gaps');
    assert.ok(gaps.some((g) => /sign-off/.test(g)));
});

test('a filled record has no gaps', () => {
    const a = emptyAssessment('scribe');
    a.signoffConfidence = 4;
    a.signoffGrounds = ['Ran the tests'];
    a.signoffVerbatim = 'It works and I checked the quote case.';
    for (const d of Object.keys(a.decisions)) {
        a.decisions[d] = 1;
        a.consistency[d] = 2;
    }
    assert.deepEqual(outstanding(a, 'scribe'), []);
});
