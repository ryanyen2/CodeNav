// The forms the researcher fills in, and the quiz the participant answers.
//
//   node --test test/forms.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseQuiz } from '../scripts/extract-questions.mjs';
import {
    OPEN_DECISIONS, FALSE_ALARMS, SETTLED_BY, CONSISTENCY, COUPLED_DECISION,
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
        assert.equal(parsed.length, 5);
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
    assert.equal(Object.keys(a.answers).length, 5 * SITTINGS.length);
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
    assert.deepEqual([result.right, result.answered, result.of], [1, 1, 5]);
});

test('every open decision has a consistency rating beside it', () => {
    // The primary outcome. Stored apart from who settled it, because a decision
    // somebody made themselves can still contradict the codebase.
    for (const project of PROJECTS) {
        const a = emptyAssessment(project);
        assert.equal(Object.keys(a.decisions).length, OPEN_DECISIONS[project].length);
        assert.deepEqual(Object.keys(a.consistency), Object.keys(a.decisions));
    }
});

test('each project plants the problems its own recording landed', () => {
    // Pinned to the recordings rather than to the design. scribe plants three,
    // because the fourth needed a request nobody would send and the recorded
    // agent did that part correctly. Rating a problem the change does not
    // contain would score every scribe session a zero on it.
    assert.equal(OPEN_DECISIONS.scribe.length, 3);
    assert.equal(OPEN_DECISIONS.tally.length, 4);
});

test('the coupled problem is named in both projects', () => {
    // It is where two rules meet, so a change that looks local is not, and it is
    // the one the tests cannot catch. In scribe the coupling survives inside the
    // first problem rather than standing alone.
    assert.deepEqual(COUPLED_DECISION, { scribe: 0, tally: 2 });
    for (const project of PROJECTS) {
        assert.ok(OPEN_DECISIONS[project][COUPLED_DECISION[project]]);
    }
});

test('consistency is rated nought to two, and says what each means', () => {
    assert.equal(CONSISTENCY.length, 3);
    for (const label of CONSISTENCY) assert.ok(label.length > 20, label);
});

test('the ways a problem can be settled are the three that matter', () => {
    assert.equal(SETTLED_BY.length, 3);
    assert.match(SETTLED_BY[2], /never noticed/);
});

test('every project has a decoy to price its false alarms against', () => {
    // Without this, a condition that made everything look suspicious would score
    // as a condition that found more.
    for (const project of PROJECTS) {
        assert.ok(FALSE_ALARMS[project]?.length, `${project} has no decoy listed`);
    }
});

test('the sign-off is no longer something this form collects', () => {
    // The participant answers it on their own page. Leaving a copy here would
    // give two records of one thing and no rule for which is right.
    const a = emptyAssessment('scribe');
    assert.ok(!('signoffConfidence' in a));
    assert.ok(!('signoffGrounds' in a));
    assert.ok(!('signoffVerbatim' in a));
});

test('gaps are named while the call is still on', () => {
    const gaps = outstanding(emptyAssessment('scribe'), 'scribe');
    assert.ok(gaps.length, 'a blank record has gaps');
    assert.ok(gaps.some((g) => /detection/.test(g)),
        'the rating that cannot be recovered afterwards is the one named');
});

test('a session with no false alarms still has to say so', () => {
    // None and not-asked look the same a week later, and only one of them is a
    // result, so a blank counts as a gap and a zero does not.
    const a = emptyAssessment('scribe');
    for (const d of Object.keys(a.decisions)) {
        a.decisions[d] = 1;
        a.consistency[d] = 2;
    }
    assert.ok(outstanding(a, 'scribe').some((g) => /false alarms/.test(g)));
    a.falseAlarms = 0;
    assert.deepEqual(outstanding(a, 'scribe'), []);
});
