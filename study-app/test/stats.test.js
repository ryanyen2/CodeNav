// The estimates the figures report.
//
// These are the numbers a reviewer will check, so the properties worth testing
// are the ones that would be wrong in a way nobody notices: the pairing, the
// seeding, and what happens when there is not enough data to say anything.
//
//   node --test test/stats.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import { rng, mean, sd, pairedDiffs, studentizedCI, pairedEstimate } from '../figures/stats.js';

test('the generator is deterministic, so a figure redraws identically', () => {
    // An unseeded bootstrap gives a different interval each time the figure is
    // drawn, so the number in the caption stops matching the picture and nobody
    // can reproduce either.
    const a = Array.from({ length: 5 }, rng(7));
    const b = Array.from({ length: 5 }, rng(7));
    assert.deepEqual(a, b);
    assert.notDeepEqual(a, Array.from({ length: 5 }, rng(8)));
    assert.ok(a.every((x) => x >= 0 && x < 1), 'and stays in range');
});

test('a difference is paired within participant', () => {
    // The design is within subjects. Treating the conditions as independent
    // samples throws away the pairing the design exists to get.
    const rows = [
        { code: 'a', condition: 'codoc', value: 6 },
        { code: 'a', condition: 'baseline', value: 2 },
        { code: 'b', condition: 'codoc', value: 5 },
        { code: 'b', condition: 'baseline', value: 4 },
    ];
    assert.deepEqual(pairedDiffs(rows), [{ code: 'a', diff: 4 }, { code: 'b', diff: 1 }]);
});

test('somebody who did only one condition contributes no difference', () => {
    // Filling their missing side from the group mean would shrink the interval
    // using a person who never provided the number.
    const rows = [
        { code: 'a', condition: 'codoc', value: 6 },
        { code: 'a', condition: 'baseline', value: 2 },
        { code: 'b', condition: 'codoc', value: 7 },
    ];
    assert.equal(pairedDiffs(rows).length, 1);
    assert.equal(pairedEstimate(rows).n, 1);
});

test('an unanswered item is absent, not zero', () => {
    const rows = [
        { code: 'a', condition: 'codoc', value: 6 },
        { code: 'a', condition: 'baseline', value: null },
    ];
    assert.deepEqual(pairedDiffs(rows), []);
});

test('under four observations there is no interval', () => {
    // An interval from three numbers is a decoration, and drawing one invites a
    // reader to take it seriously.
    assert.equal(studentizedCI([1, 2, 3]), null);
    assert.ok(studentizedCI([1, 2, 3, 4]));
});

test('the interval brackets the mean and is not backwards', () => {
    const xs = [2, 3, 3, 4, 5, 5, 6, 7];
    const ci = studentizedCI(xs, { seed: 1 });
    assert.equal(ci.mean, mean(xs));
    assert.ok(ci.low < ci.mean, `low ${ci.low} should sit below the mean ${ci.mean}`);
    assert.ok(ci.high > ci.mean, `high ${ci.high} should sit above the mean`);
});

test('more data gives a tighter interval', () => {
    const wide = studentizedCI([1, 3, 5, 7, 9], { seed: 3 });
    const many = studentizedCI(Array.from({ length: 40 }, (_, i) => [1, 3, 5, 7, 9][i % 5]),
        { seed: 3 });
    assert.ok(many.high - many.low < wide.high - wide.low,
        'forty observations of the same spread must say more than five');
});

test('a sample with no spread says so instead of pretending to an interval', () => {
    // Common in ordinal data: everybody answers 6. The interval is the point, and
    // marking it degenerate is what stops the figure drawing a bar of width zero
    // that reads as extreme precision.
    const ci = studentizedCI([6, 6, 6, 6, 6, 6], { seed: 1 });
    assert.equal(ci.degenerate, true);
    assert.equal(ci.low, ci.high);
});

test('the same data gives the same interval every time', () => {
    const xs = [2, 3, 3, 4, 5, 5, 6, 7];
    const a = studentizedCI(xs, { seed: 20260816 });
    const b = studentizedCI(xs, { seed: 20260816 });
    assert.deepEqual(a, b, 'a figure redrawn must not move');
});

test('a real difference is separated from zero, and no difference is not', () => {
    // The property the panel exists for. If this fails, the figure reports
    // significance where there is none, or hides it where there is.
    const real = Array.from({ length: 12 }, (_, i) => ({ code: `p${i}`, value: 6 }))
        .flatMap((p) => [
            { code: p.code, condition: 'codoc', value: 6 },
            { code: p.code, condition: 'baseline', value: 2 + (i => i % 2)(0) },
        ]);
    const e1 = pairedEstimate(real, { seed: 1 });
    assert.ok(e1.low > 0, `a four point difference should clear zero, got ${e1.low}`);

    const none = Array.from({ length: 12 }, (_, i) => [
        { code: `p${i}`, condition: 'codoc', value: 4 + (i % 3) - 1 },
        { code: `p${i}`, condition: 'baseline', value: 4 + ((i + 1) % 3) - 1 },
    ]).flat();
    const e2 = pairedEstimate(none, { seed: 1 });
    assert.ok(e2.low < 0 && e2.high > 0,
        `no real difference should straddle zero, got ${e2.low} to ${e2.high}`);
});

test('the direction is the first condition minus the second', () => {
    // Getting this backwards flips every bar on the figure and nothing errors.
    const rows = [
        { code: 'a', condition: 'codoc', value: 7 },
        { code: 'a', condition: 'baseline', value: 1 },
    ];
    assert.equal(pairedDiffs(rows)[0].diff, 6);
    assert.equal(pairedDiffs(rows, { a: 'baseline', b: 'codoc' })[0].diff, -6);
});

test('mean and sd behave on the edges', () => {
    assert.equal(mean([]), null);
    assert.equal(sd([5]), 0, 'one observation has no spread to report');
    assert.equal(mean([2, 4]), 3);
});
