// The planned cohort: balance by construction, and the ways it quietly breaks.
//
//   node --test test/cohort.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import { plan, fill, nextOrder, progress, isPilot, PILOTS, PARTICIPANTS } from '../shared/cohort.js';
import { newParticipantCode, isPilotCode, CODE_PATTERN } from '../shared/schema.js';

const person = (i, over = {}) => ({ code: `p-${i}`, createdAt: i, order: 'codoc-first', ...over });

test('the plan is two pilots and twelve participants', () => {
    const slots = plan();
    assert.equal(slots.filter((s) => s.kind === 'pilot').length, PILOTS);
    assert.equal(slots.filter((s) => s.kind === 'participant').length, PARTICIPANTS);
    assert.equal(slots.filter((s) => s.kind === 'pilot').length, 2);
});

test('the twelve are balanced across both orders', () => {
    const ps = plan().filter((s) => s.kind === 'participant');
    const counts = {};
    for (const s of ps) counts[s.order] = (counts[s.order] || 0) + 1;
    assert.deepEqual(counts, { 'codoc-first': 6, 'baseline-first': 6 });
});

test('orders alternate rather than running in blocks', () => {
    // Recruitment usually stops early. Alternating leaves a balanced half;
    // blocked would leave everyone remaining in the same condition first.
    const first6 = plan().filter((s) => s.kind === 'participant').slice(0, 6);
    const counts = {};
    for (const s of first6) counts[s.order] = (counts[s.order] || 0) + 1;
    assert.deepEqual(counts, { 'codoc-first': 3, 'baseline-first': 3 },
        'stopping halfway must still leave a balanced design');
});

test('slots exist before anybody does', () => {
    // The question mid-study is "how many more, in which order", and a list of
    // only what exists cannot answer it.
    const { slots } = fill([]);
    assert.equal(slots.length, PILOTS + PARTICIPANTS);
    assert.ok(slots.every((s) => s.participant === null));
});

test('people land in slots of their own kind, oldest first', () => {
    const existing = [person(3), person(1, { pilot: true }), person(2)];
    const { slots } = fill(existing);
    const pilots = slots.filter((s) => s.kind === 'pilot');
    const ps = slots.filter((s) => s.kind === 'participant');
    assert.equal(pilots[0].participant.code, 'p-1');
    assert.equal(pilots[1].participant, null);
    assert.equal(ps[0].participant.code, 'p-2', 'created earlier takes the earlier slot');
    assert.equal(ps[1].participant.code, 'p-3');
});

test('the plan grows to hold whoever exists', () => {
    // Two pilots and twelve is the intention, not a limit. A thirteenth used to
    // land in a "beyond the plan" pile where nothing counted it, which is the
    // wrong answer to a study that turned out to need one more.
    const existing = Array.from({ length: PARTICIPANTS + 3 }, (_, i) => person(i));
    const { slots, extra } = fill(existing);
    assert.equal(extra.length, 0, 'nobody falls off the end');
    assert.equal(slots.filter((s) => s.kind === 'participant').length, PARTICIPANTS + 3);
    assert.equal(progress(existing).participants.of, PARTICIPANTS + 3,
        'and the denominator says how many there now are');
});

test('a third pilot is a pilot, not an overflow', () => {
    const existing = Array.from({ length: 3 }, (_, i) => person(i, { pilot: true }));
    const p = progress(existing);
    assert.equal(p.pilots.filled, 3);
    assert.equal(p.pilots.of, 3);
    assert.equal(p.analysable, 0, 'and still none of them are analysed');
});

test('the next order is whatever the plan says is open', () => {
    assert.equal(nextOrder([], 'pilot'), 'codoc-first');
    assert.equal(nextOrder([person(1, { pilot: true })], 'pilot'), 'baseline-first');
    assert.equal(nextOrder([]), 'codoc-first');
    assert.equal(nextOrder([person(1)]), 'baseline-first');
});

test('past the plan it keeps the halves even instead of repeating', () => {
    const full = Array.from({ length: PARTICIPANTS }, (_, i) =>
        person(i, { order: i % 2 ? 'baseline-first' : 'codoc-first' }));
    // One more of each, then the next should go to whichever is behind.
    const lopsided = [...full, person(99, { order: 'codoc-first' })];
    assert.equal(nextOrder(lopsided), 'baseline-first');
});

test('pilots are not analysed', () => {
    // A pilot exists to find out that the instrument is broken. A study that
    // quietly analyses them has no way left to say so.
    const existing = [person(1, { pilot: true }), person(2), person(3)];
    const p = progress(existing);
    assert.equal(p.analysable, 2);
    assert.equal(p.pilots.filled, 1);
    assert.equal(p.participants.filled, 2);
});

test('an excluded participant leaves their slot counted but not analysed', () => {
    const existing = [person(1), person(2, { excluded: true })];
    const p = progress(existing);
    assert.equal(p.participants.filled, 2, 'the session still happened');
    assert.equal(p.analysable, 1);
    assert.equal(p.excluded, 1);
});

test('exclusions that unbalance the design are surfaced', () => {
    // The quiet failure: two people excluded who happened to share an order, and
    // nothing says the remaining design is lopsided.
    const existing = [
        person(1, { order: 'codoc-first' }),
        person(2, { order: 'codoc-first' }),
        person(3, { order: 'baseline-first', excluded: true }),
        person(4, { order: 'baseline-first', excluded: true }),
    ];
    const p = progress(existing);
    assert.deepEqual(p.byOrder, { 'codoc-first': 2, 'baseline-first': 0 });
    assert.equal(p.imbalance, 2);
});

test('a balanced cohort reports no imbalance', () => {
    const existing = [
        person(1, { order: 'codoc-first' }), person(2, { order: 'baseline-first' }),
    ];
    assert.equal(progress(existing).imbalance, 0);
});

// ── telling a pilot from a participant ───────────────────────────────────────

test('a pilot says so in its own code', () => {
    // The flag lives in Firestore and stops there. An exported JSON, a CSV of
    // figure data, a collected zip and a log line all carry the code and none of
    // them carried the flag, so a pilot left the dashboard indistinguishable
    // from a real participant. The prefix goes everywhere the code goes.
    const pilot = newParticipantCode('pilot');
    const person = newParticipantCode('participant');
    assert.ok(isPilotCode(pilot));
    assert.ok(!isPilotCode(person));
    assert.match(pilot, /^pilot-/);
    assert.match(person, /^p-/);
});

test('both shapes are still valid codes', () => {
    // setup.sh, the participant page and the rules all take a code. A new shape
    // that any of them refuses would fail at the worst moment.
    for (const kind of ['pilot', 'participant']) {
        assert.match(newParticipantCode(kind), CODE_PATTERN);
    }
});

test('a pilot code is not mistaken for a participant, or the reverse', () => {
    // 'pilot-' also starts with 'p', which is exactly the kind of near-miss that
    // makes a prefix scheme quietly wrong.
    assert.ok(!isPilotCode('p-abcdefghjkmn'));
    assert.ok(isPilotCode('pilot-abcdefghjkmn'));
    assert.ok(!isPilotCode(''));
    assert.ok(!isPilotCode(null));
});

test('a record that lost its flag is still treated as a pilot', () => {
    // The reason both are checked. A copy, an export or a hand-edited row can
    // drop a boolean; it cannot drop the code.
    assert.ok(isPilot({ code: 'pilot-abcdefghjkmn' }), 'by the code alone');
    assert.ok(isPilot({ code: 'p-abcdefghjkmn', pilot: true }), 'by the flag alone');
    assert.ok(!isPilot({ code: 'p-abcdefghjkmn' }));
});

test('a pilot known only by its code is still kept out of the analysis', () => {
    const existing = [
        { code: 'pilot-aaaaaaaaaaaa', createdAt: 1, order: 'codoc-first' },  // no flag
        { code: 'p-bbbbbbbbbbbb', createdAt: 2, order: 'codoc-first' },
    ];
    const p = progress(existing);
    assert.equal(p.pilots.filled, 1, 'it fills a pilot slot');
    assert.equal(p.analysable, 1, 'and only the real participant is analysed');
});
