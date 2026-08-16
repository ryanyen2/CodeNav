// Counting patterns, and refusing to invent them.
//
//   node --test test/ngrams.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import { toEpisodes, comparableEpisodes, letters, collapseRuns, describe } from '../analysis/sequences.js';
import { count, unigrams, score, scoreBySession, compare, label } from '../analysis/ngrams.js';

const at = (t, a, ms = 0) => ({ t, a, spanMs: ms });

// ── episodes ─────────────────────────────────────────────────────────────────

test('a break splits the session rather than joining across it', () => {
    // Without this, whatever came before lunch is counted as leading to whatever
    // came after, and enough of those make lunch the study's main finding.
    const { episodes } = toEpisodes([
        at(0, 'READ_DOC'), at(1000, 'READ_CODE'), at(2000, 'PROMPT'),
        { t: 3000, a: 'IDLE', ms: 600_000 },
        at(603_000, 'RUN_TEST'), at(604_000, 'READ_CODE'), at(605_000, 'EDIT_CODE'),
    ]);
    assert.equal(episodes.length, 2);
    assert.deepEqual(letters(episodes[0]), ['READ_DOC', 'READ_CODE', 'PROMPT']);
    assert.deepEqual(letters(episodes[1]), ['RUN_TEST', 'READ_CODE', 'EDIT_CODE']);
});

test('a raw gap splits too, even with no idle marker', () => {
    const { episodes } = toEpisodes([
        at(0, 'READ_DOC'), at(1000, 'READ_CODE'), at(2000, 'PROMPT'),
        at(400_000, 'RUN_TEST'), at(401_000, 'READ_CODE'), at(402_000, 'EDIT_CODE'),
    ]);
    assert.equal(episodes.length, 2);
});

test('episodes too short to show an order are dropped, and counted', () => {
    const { episodes, droppedActions } = toEpisodes([
        at(0, 'READ_DOC'), at(1000, 'READ_CODE'),
        { t: 2000, a: 'IDLE', ms: 600_000 },
        at(603_000, 'PROMPT'), at(604_000, 'AGENT_EDIT'), at(605_000, 'RUN_TEST'),
    ]);
    assert.equal(episodes.length, 1);
    assert.equal(droppedActions, 2, 'what was dropped is reported, not hidden');
});

test('a long look is not a break', () => {
    const { episodes } = toEpisodes([
        at(0, 'READ_CODE', 300_000), at(300_000, 'PROMPT'), at(301_000, 'AGENT_EDIT'),
    ]);
    assert.equal(episodes.length, 1, 'time spent reading is time spent working');
});

test('a comparison never sees the codoc-only actions', () => {
    const actions = [
        at(0, 'READ_DOC'), at(1000, 'ACCEPT'), at(2000, 'READ_CODE'),
        at(3000, 'REJECT'), at(4000, 'EDIT_CODE'),
    ];
    const { episodes } = comparableEpisodes(actions);
    assert.deepEqual(letters(episodes[0]), ['READ_DOC', 'READ_CODE', 'EDIT_CODE']);
});

test('collapsing runs is offered but not applied by default', () => {
    // Three files read in a row is either one act of orientation or three acts of
    // navigation. That is a question about the person, not a cleanup step.
    const seq = ['READ_CODE', 'READ_CODE', 'READ_CODE', 'PROMPT'];
    assert.deepEqual(collapseRuns(seq), ['READ_CODE', 'PROMPT']);
    const { episodes } = toEpisodes([at(0, 'READ_CODE'), at(1, 'READ_CODE'), at(2, 'READ_CODE')]);
    assert.equal(episodes[0].length, 3, 'left alone unless asked');
});

// ── counting ─────────────────────────────────────────────────────────────────

test('pairs and triples are counted inside episodes, never across them', () => {
    const episodes = [['A', 'B', 'C'], ['D', 'E']];
    const pairs = count(episodes, 2);
    assert.equal(pairs.total, 3);           // AB BC DE, and never CD
    assert.equal(pairs.grams.get('C D'), undefined);
    assert.equal(count(episodes, 3).total, 1);
});

test('what is common is separated from what recurs', () => {
    // READ_CODE is everywhere, so READ_CODE READ_CODE turns up often by
    // arithmetic alone. PROMPT AGENT_EDIT is rarer but almost always together.
    const episodes = [];
    for (let i = 0; i < 20; i += 1) {
        episodes.push(['READ_CODE', 'READ_CODE', 'READ_CODE', 'READ_CODE', 'PROMPT', 'AGENT_EDIT']);
    }
    const { rows } = score(episodes, { n: 2, minCount: 3 });
    const common = rows.find((r) => r.gram === 'READ_CODE READ_CODE');
    const real = rows.find((r) => r.gram === 'PROMPT AGENT_EDIT');

    assert.ok(common.count > real.count, 'the common one happens more often');
    assert.ok(real.lift > common.lift, 'but the real pattern scores higher');
    assert.equal(rows[0].gram, 'PROMPT AGENT_EDIT', 'and it ranks first');
});

test('a pair that happens exactly as often as its parts predict scores near zero', () => {
    // Alternating A and B: every pair is as likely as chance would make it.
    const episodes = [Array.from({ length: 40 }, (_, i) => (i % 2 ? 'A' : 'B'))];
    const { rows } = score(episodes, { n: 2, minCount: 2 });
    for (const r of rows) {
        assert.ok(Math.abs(r.lift) < 1.1, `${r.gram} scored ${r.lift}, expected near zero`);
    }
});

test('the tail is trimmed and the amount is reported', () => {
    const episodes = [['A', 'B', 'A', 'B', 'A', 'B', 'C', 'D']];
    const s = score(episodes, { n: 2, minCount: 3 });
    assert.ok(s.trimmed > 0, 'rare pairs were dropped');
    assert.ok(s.trimmedShare > 0 && s.trimmedShare < 1, 'and how much of the data that was');
    assert.ok(!s.rows.some((r) => r.count < 3));
});

// ── one session must not speak for the group ─────────────────────────────────

test('a single long session cannot dominate the group', () => {
    // One participant who worked twice as fast contributes twice the pairs. If
    // counts are pooled, their habits become the study's findings.
    // One session with four hundred actions, five with six each.
    const busy = [Array.from({ length: 400 }, (_, i) => (i % 2 ? 'X' : 'Y'))];
    const quiet = Array.from({ length: 5 }, () => [['P', 'Q', 'P', 'Q', 'P', 'Q']]);
    const sessions = [busy, ...quiet];

    const pooled = score(sessions.flat(), { n: 2, minCount: 2 });
    const perSession = scoreBySession(sessions, { n: 2, minCount: 2, minSessions: 1 });

    const pooledXY = pooled.rows.find((r) => r.gram === 'X Y');
    const pooledPQ = pooled.rows.find((r) => r.gram === 'P Q');
    assert.ok(pooledXY.count > pooledPQ.count * 5,
        'pooled, the busiest session supplies most of the pairs');

    const pq = perSession.rows.find((r) => r.gram === 'P Q');
    const xy = perSession.rows.find((r) => r.gram === 'X Y');
    assert.equal(pq.sessions, 5);
    assert.equal(xy.sessions, 1);
    assert.ok(pq.meanShare > xy.meanShare,
        'per session, what five people share outranks what one person did a lot of');
});

test('a habit only one person has is not a finding about the group', () => {
    const sessions = [
        [['A', 'B', 'A', 'B', 'A', 'B']],   // only this participant
        [['C', 'D', 'C', 'D']],
        [['C', 'D', 'C', 'D']],
    ];
    const { rows, trimmed } = scoreBySession(sessions, { n: 2, minCount: 2, minSessions: 2 });
    assert.ok(!rows.some((r) => r.gram === 'A B'), 'one participant is not a pattern');
    assert.ok(rows.some((r) => r.gram === 'C D'));
    assert.ok(trimmed > 0);
});

// ── comparing the conditions ─────────────────────────────────────────────────

test('the two conditions are compared on shares, not on raw counts', () => {
    // A session is a list of episodes, and each episode is a list of actions.
    const withCodoc = [
        [['READ_DOC', 'EDIT_DOC', 'READ_DOC', 'EDIT_DOC']],
        [['READ_DOC', 'EDIT_DOC', 'READ_DOC', 'EDIT_DOC']],
    ];
    const without = [
        [['READ_CODE', 'PROMPT', 'READ_CODE', 'PROMPT']],
        [['READ_CODE', 'PROMPT', 'READ_CODE', 'PROMPT']],
    ];
    const { rows } = compare(withCodoc, without, { minCount: 2, minSessions: 2 });

    const docLoop = rows.find((r) => r.gram === 'READ_DOC EDIT_DOC');
    const codeLoop = rows.find((r) => r.gram === 'READ_CODE PROMPT');
    assert.ok(docLoop.a > 0 && docLoop.b === 0, 'one appears in one condition only');
    assert.ok(codeLoop.b > 0 && codeLoop.a === 0);
    assert.ok(rows.every((r) => r.a >= 0 && r.b >= 0 && r.a <= 1 && r.b <= 1),
        'shares, not counts');
});

// ── describing what the counts rest on ───────────────────────────────────────

test('a lopsided set of sessions says so', () => {
    const even = describe([
        { actions: Array(100).fill({ a: 'READ_CODE' }) },
        { actions: Array(110).fill({ a: 'READ_CODE' }) },
        { actions: Array(90).fill({ a: 'READ_CODE' }) },
    ]);
    assert.equal(even.lopsided, false);

    const skewed = describe([
        { actions: Array(20).fill({ a: 'READ_CODE' }) },
        { actions: Array(20).fill({ a: 'READ_CODE' }) },
        { actions: Array(900).fill({ a: 'READ_CODE' }) },
    ]);
    assert.equal(skewed.lopsided, true, 'so a reader knows before reading the counts');
});

test('a set containing codoc-only actions is flagged as not comparable', () => {
    const s = describe([{ actions: [{ a: 'READ_DOC' }, { a: 'ACCEPT' }] }]);
    assert.equal(s.allShared, false);
});

test('a pattern is written the way it would be said', () => {
    assert.equal(label('PROMPT AGENT_EDIT RUN_TEST'), 'prompt → agent edit → run test');
});
