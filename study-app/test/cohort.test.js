// The planned cohort: balance by construction, and the ways it quietly breaks.
//
//   node --test test/cohort.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import { plan, fill, nextOrder, progress, PILOTS, PARTICIPANTS } from '../shared/cohort.js';

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

test('more people than slots is visible rather than dropped', () => {
    const existing = Array.from({ length: PARTICIPANTS + 3 }, (_, i) => person(i));
    const { slots, extra } = fill(existing);
    assert.equal(extra.length, 3);
    assert.equal(slots.filter((s) => s.kind === 'participant' && s.participant).length, PARTICIPANTS);
    assert.equal(progress(existing).extra, 3);
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
