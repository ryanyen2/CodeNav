// The era map, pinned.
//
// These tests exist because the map is a set of claims about dates, and a date
// that drifts is a date nobody notices drifting. The interesting cases are all
// about REFUSING: an instant inside a deploy window must not be placed, a
// measure broken by a boundary must not pool across it, and a defect window must
// not condemn sessions that ran before the defective code existed.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
    BOUNDARIES, ERAS, MEASURES, eraFor, defectsFor, poolable,
} from '../shared/instrument-eras.js';
import { AFTER_CONDITION } from '../participant/instrument.js';

const at = (iso) => Date.parse(iso);

test('every boundary carries a real instant and a deploy window after it', () => {
    for (const b of BOUNDARIES) {
        assert.ok(Number.isFinite(b.at), `${b.id} has no instant`);
        assert.ok(b.uncertainUntil > b.at, `${b.id} has no deploy window`);
        assert.ok(b.changes.length, `${b.id} says nothing about what changed`);
    }
});

test('the eras tile the timeline with no gap and no overlap', () => {
    for (let i = 1; i < ERAS.length; i += 1) {
        assert.equal(ERAS[i].from, ERAS[i - 1].to, `gap between ${ERAS[i - 1].id} and ${ERAS[i].id}`);
    }
    assert.equal(ERAS[0].from, null, 'the first era must be open at the start');
    assert.equal(ERAS[ERAS.length - 1].to, null, 'the last era must be open at the end');
});

test('an instant inside a deploy window is refused rather than placed', () => {
    // Two hours after the ctl4 flip was committed, which is exactly when nobody
    // can say whether the site had been rebuilt.
    const r = eraFor(at('2026-08-22T20:05:00-04:00'));
    assert.equal(r.unplaceable, true);
    assert.equal(r.near, 'ctl4-flip');
});

test('an instant clear of every window is placed without a flag', () => {
    const r = eraFor(at('2026-08-18T12:00:00-04:00'));
    assert.equal(r.unplaceable, false);
    assert.equal(r.era.id, 'era-0');
});

test('a missing timestamp is unplaceable, not era zero', () => {
    // The failure this guards is the one that looks like data: a participant with
    // no clock silently sorting to the front and being read as the earliest run.
    const r = eraFor(null);
    assert.equal(r.era, null);
    assert.equal(r.unplaceable, true);
});

test('the two published blocks pool across every boundary in the study', () => {
    const all = ERAS.map((e) => e.id);
    assert.equal(poolable('umux', all).ok, true);
    assert.equal(poolable('tlx', all).ok, true);
});

test('ctl4 does not pool across its flip, and says it is recoverable', () => {
    const v = poolable('ctl4', ['era-2', 'era-3']);
    assert.equal(v.ok, false);
    assert.equal(v.recoverable, true);
});

test('ctl4 pools within one side of the flip', () => {
    assert.equal(poolable('ctl4', ['era-0', 'era-1', 'era-2']).ok, true);
    assert.equal(poolable('ctl4', ['era-3', 'era-4']).ok, true);
});

test('detection coverage is broken by both of its boundaries and by neither in between', () => {
    // Between 08-20 and 08-24 nothing touched it, and that gap is where most of
    // the cohort is likely to sit. Saying so is the difference between losing a
    // confirmatory measure and keeping it.
    assert.equal(poolable('detection.coverage', ['era-2', 'era-3']).ok, true);
    assert.equal(poolable('detection.coverage', ['era-1', 'era-2']).ok, false);
    assert.equal(poolable('detection.coverage', ['era-3', 'era-4']).ok, false);
});

test('the durable written trace survives every boundary', () => {
    // The other pre-registered prediction. analysis-plan.md states it is
    // untouched by the change that killed detection, and if that ever stops
    // being true the study has no confirmatory measure left at all.
    const all = ERAS.map((e) => e.id);
    const v = poolable('trace.durable', all);
    assert.equal(v.ok, true);
    assert.equal(MEASURES.find((m) => m.id === 'trace.durable').confirmatory, true);
});

test('the instrumentation defect flags its window and nothing before it', () => {
    // Before the replay existed there was no wipe to suffer.
    assert.deepEqual(defectsFor(at('2026-08-18T12:00:00-04:00')), []);
    assert.deepEqual(defectsFor(at('2026-08-21T12:00:00-04:00')), ['replay-wiped-instrumentation']);
    assert.deepEqual(defectsFor(at('2026-08-27T12:00:00-04:00')), []);
});

test('every measure that names items names ids the instrument still has', () => {
    // The map is only worth reading if its item ids match the ones answers are
    // stored under. A measure whose items were renamed would report 0 held for
    // everybody and read as a study that collected nothing.
    const live = new Set(AFTER_CONDITION.map((q) => q.id));
    const removed = new Set(['noticedChange', 'openQ', 'recall']);
    const computed = new Set(['correct', 'confidence', 'grounds', 'unsure',
        'startedAt', 'finishedAt', 'elapsedMs',
        's1', 's2', 's3', 's4', 's5', 's6', 's7']);
    for (const m of MEASURES) {
        for (const it of m.items || []) {
            if (removed.has(it) || computed.has(it)) continue;
            assert.ok(live.has(it), `${m.id} names ${it}, which the instrument no longer has`);
        }
    }
});

test('ctl4 is scored on its own, never inside the control construct', () => {
    // The construct mean must not quietly average an item that means opposite
    // things in two halves of the cohort. Keeping ctl4 out of `control` is what
    // makes the default safe when nobody has read this file.
    const control = MEASURES.find((m) => m.id === 'control');
    assert.ok(!control.items.includes('ctl4'));
    assert.deepEqual(control.items, ['ctl1', 'ctl2', 'ctl3', 'ctl5']);
});
