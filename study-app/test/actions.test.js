// What the vocabulary must do, and refuse to do.
//
//   node --test test/actions.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import {
    ACTIONS, SHARED, CODOC_ONLY, isShared,
    mapEvent, toSequence, toLetters, sharedOnly, DEFAULTS,
} from '../shared/actions.js';
import { FORBIDDEN_FIELDS, hasForbiddenField, newParticipantCode, CODE_PATTERN } from '../shared/schema.js';

// ── the list itself ──────────────────────────────────────────────────────────

test('every action in the list is produced by at least one input, so none is dead', () => {
    const produced = new Set();
    const samples = [
        { ev: 'view', surface: 'document', t: 5000, ms: 2000 },
        { ev: 'view', surface: 'code', t: 5000, ms: 2000 },
        { ev: 'view', surface: 'test', t: 5000, ms: 2000 },
        { ev: 'edit', surface: 'document', t: 1, active: true, focused: true },
        { ev: 'edit', surface: 'code', t: 1, active: true, focused: true },
        { ev: 'edit', surface: 'test', t: 1, active: true, focused: true },
        { ev: 'edit', surface: 'code', t: 1, active: false, focused: true },
        { ev: 'edit', surface: 'document', t: 1, active: false, focused: true },
        { ev: 'prompt', t: 1, chars: 40 },
        { ev: 'agent', t: 1, cmd: 'pytest' },
        { ev: 'agent', t: 1, cmd: 'hearth' },
        { ev: 'codoc', t: 1, kind: 'verdict', accept: true },
        { ev: 'codoc', t: 1, kind: 'verdict', accept: false },
    ];
    for (const s of samples) {
        const a = mapEvent(s);
        if (a) produced.add(a.a);
    }
    produced.add('IDLE');   // derived from gaps, not from any single event
    for (const name of ACTIONS) {
        assert.ok(produced.has(name), `${name} is in the list but nothing produces it`);
    }
});

test('the shared level contains no action only one condition can produce', () => {
    for (const a of CODOC_ONLY) assert.ok(!SHARED.includes(a), `${a} must not be shared`);
    assert.ok(!SHARED.includes('ACCEPT'));
    assert.ok(!SHARED.includes('REJECT'));
    assert.ok(isShared('READ_DOC') && !isShared('ACCEPT'));
});

test('the vocabulary is closed: an unknown event maps to nothing, not to a catch-all', () => {
    assert.equal(mapEvent({ ev: 'window', t: 1, focused: false }), null);
    assert.equal(mapEvent({ ev: 'session', t: 1, start: true }), null);
    assert.equal(mapEvent({ ev: 'save', t: 1, surface: 'code' }), null);
    assert.equal(mapEvent({ ev: 'nonsense', t: 1 }), null);
    assert.equal(mapEvent(null), null);
    assert.ok(!ACTIONS.includes('OTHER'), 'a catch-all would swallow the tail');
});

// ── looking ──────────────────────────────────────────────────────────────────

test('the description maps to the same action in both conditions', () => {
    // One is a custom editor, the other an ordinary markdown file. The point of
    // the mapper is that the analysis cannot tell them apart.
    const codoc = mapEvent({ ev: 'view', surface: 'document', file: '.codoc/tree.codoc', t: 9000, ms: 3000 });
    const baseline = mapEvent({ ev: 'view', surface: 'document', file: 'CLAUDE.md', t: 9000, ms: 3000 });
    assert.equal(codoc.a, 'READ_DOC');
    assert.equal(baseline.a, 'READ_DOC');
});

test('a look is filed at the moment it started, not when it ended', () => {
    // The logger reports a view when the file leaves the screen. Filing it at that
    // time would put every look after whatever happened during it.
    const a = mapEvent({ ev: 'view', surface: 'code', file: 'x.py', t: 10_000, ms: 4000 });
    assert.equal(a.t, 6000);
    assert.equal(a.ms, 4000);
});

// ── changing ─────────────────────────────────────────────────────────────────

test('typing and a file changing underneath map to different actions', () => {
    const typed = mapEvent({ ev: 'edit', surface: 'code', file: 'a.py', t: 1, active: true, focused: true, added: 5 });
    const rewritten = mapEvent({ ev: 'edit', surface: 'code', file: 'a.py', t: 1, active: false, focused: true, added: 200 });
    assert.equal(typed.a, 'EDIT_CODE');
    assert.equal(rewritten.a, 'AGENT_EDIT');
});

test('an edit while the window is not focused is not counted as the person typing', () => {
    const a = mapEvent({ ev: 'edit', surface: 'code', file: 'a.py', t: 1, active: true, focused: false, added: 9 });
    assert.equal(a.a, 'AGENT_EDIT');
});

test('the agent changing the description is its own action', () => {
    const a = mapEvent({ ev: 'edit', surface: 'document', file: 'CLAUDE.md', t: 1, active: false, focused: true, added: 80 });
    assert.equal(a.a, 'AGENT_DOC');
});

test('a description edit through codoc arrives from the ledger, not as a text edit', () => {
    // In the codoc condition the description is edited in a custom editor, so no
    // text edit ever reaches the logger. Both routes must land on one action.
    const viaLedger = mapEvent({ ev: 'codoc', t: 5, kind: 'amend', actor: 'human', feature: 'f-1' });
    const viaText = mapEvent({ ev: 'edit', surface: 'document', file: 'CLAUDE.md', t: 5, active: true, focused: true });
    assert.equal(viaLedger.a, 'EDIT_DOC');
    assert.equal(viaText.a, 'EDIT_DOC');
});

test('codoc work done by the loop is not attributed to the person', () => {
    const a = mapEvent({ ev: 'codoc', t: 5, kind: 'amend', actor: 'loop_a_agent', feature: 'f-1' });
    assert.equal(a.a, 'AGENT_DOC');
});

test('bookkeeping ops in the ledger are not actions', () => {
    assert.equal(mapEvent({ ev: 'codoc', t: 1, kind: 'attach', actor: 'loop' }), null);
    assert.equal(mapEvent({ ev: 'codoc', t: 1, kind: 'refresh', actor: 'loop' }), null);
});

// ── running and instructing ──────────────────────────────────────────────────

test('running the tests and building are told apart, and nothing else maps', () => {
    assert.equal(mapEvent({ ev: 'agent', t: 1, cmd: 'pytest' }).a, 'RUN_TEST');
    assert.equal(mapEvent({ ev: 'agent', t: 1, cmd: '/usr/bin/pytest' }).a, 'RUN_TEST');
    assert.equal(mapEvent({ ev: 'agent', t: 1, cmd: 'hearth' }).a, 'RUN_BUILD');
    assert.equal(mapEvent({ ev: 'agent', t: 1, cmd: 'ls' }), null);
    assert.equal(mapEvent({ ev: 'agent', t: 1, cmd: 'git' }), null);
});

test('a prompt carries its length and how many steps it took to write', () => {
    const a = mapEvent({ ev: 'prompt', t: 1, chars: 140, steps: 3 });
    assert.equal(a.a, 'PROMPT');
    assert.equal(a.chars, 140);
    assert.equal(a.steps, 3);
});

// ── events to a sequence ─────────────────────────────────────────────────────

test('typing a paragraph is one action, not one per keystroke', () => {
    const events = [];
    for (let i = 0; i < 40; i++) {
        events.push({ ev: 'edit', surface: 'code', file: 'a.py', t: 1000 + i * 100, active: true, focused: true, added: 3 });
    }
    const seq = toSequence(events);
    assert.equal(seq.length, 1, 'forty keystrokes should read as one edit');
    assert.equal(seq[0].a, 'EDIT_CODE');
    assert.equal(seq[0].added, 120, 'the totals survive the join');
    assert.equal(seq[0].count, 40);
});

test('edits to different files stay separate', () => {
    const seq = toSequence([
        { ev: 'edit', surface: 'code', file: 'a.py', t: 1000, active: true, focused: true, added: 3 },
        { ev: 'edit', surface: 'code', file: 'b.py', t: 1100, active: true, focused: true, added: 3 },
    ]);
    assert.deepEqual(toLetters(seq), ['EDIT_CODE', 'EDIT_CODE']);
    assert.equal(seq[0].file, 'a.py');
    assert.equal(seq[1].file, 'b.py');
});

test('a long gap becomes exactly one idle, not one per second', () => {
    const seq = toSequence([
        { ev: 'edit', surface: 'code', file: 'a.py', t: 0, active: true, focused: true },
        { ev: 'edit', surface: 'code', file: 'a.py', t: 600_000, active: true, focused: true },
    ]);
    assert.deepEqual(toLetters(seq), ['EDIT_CODE', 'IDLE', 'EDIT_CODE']);
    assert.equal(seq.filter((s) => s.a === 'IDLE').length, 1);
    assert.ok(seq[1].ms >= 590_000);
});

test('a gap under the threshold is not an idle', () => {
    const seq = toSequence([
        { ev: 'edit', surface: 'code', file: 'a.py', t: 0, active: true, focused: true },
        { ev: 'edit', surface: 'code', file: 'a.py', t: DEFAULTS.idleGapMs - 1000, active: true, focused: true },
    ]);
    assert.ok(!toLetters(seq).includes('IDLE'));
});

test('a long look does not become an idle just because it took a while', () => {
    // The gap is measured from the end of an action, so reading one file for five
    // minutes is reading, not a break.
    const seq = toSequence([
        { ev: 'view', surface: 'code', file: 'a.py', t: 300_000, ms: 300_000 },
        { ev: 'edit', surface: 'code', file: 'a.py', t: 310_000, active: true, focused: true },
    ]);
    assert.deepEqual(toLetters(seq), ['READ_CODE', 'EDIT_CODE']);
});

test('events arriving out of order are put back in order', () => {
    const seq = toSequence([
        { ev: 'prompt', t: 5000, chars: 10 },
        { ev: 'edit', surface: 'code', file: 'a.py', t: 1000, active: true, focused: true },
    ]);
    assert.deepEqual(toLetters(seq), ['EDIT_CODE', 'PROMPT']);
});

test('a comparison between conditions cannot include the codoc-only actions', () => {
    const seq = toSequence([
        { ev: 'view', surface: 'document', file: 'x', t: 2000, ms: 1000 },
        { ev: 'codoc', t: 3000, kind: 'verdict', accept: true },
        { ev: 'edit', surface: 'code', file: 'a.py', t: 4000, active: true, focused: true },
    ]);
    assert.ok(toLetters(seq).includes('ACCEPT'));
    assert.deepEqual(toLetters(sharedOnly(seq)), ['READ_DOC', 'EDIT_CODE']);
});

test('a realistic stretch reads as something a person would recognise', () => {
    const seq = toSequence([
        { ev: 'view', surface: 'document', file: 'CLAUDE.md', t: 12_000, ms: 12_000 },
        { ev: 'view', surface: 'code', file: 'ember/digest.py', t: 40_000, ms: 20_000 },
        { ev: 'prompt', t: 45_000, chars: 180 },
        { ev: 'edit', surface: 'code', file: 'ember/digest.py', t: 60_000, active: false, focused: true, added: 400 },
        { ev: 'agent', t: 70_000, cmd: 'pytest' },
    ]);
    assert.deepEqual(toLetters(seq),
        ['READ_DOC', 'READ_CODE', 'PROMPT', 'AGENT_EDIT', 'RUN_TEST']);
});

// ── the guard that keeps names out ───────────────────────────────────────────

test('the forbidden field list is what the rules enforce', () => {
    for (const f of ['name', 'email', 'phone', 'address']) {
        assert.ok(FORBIDDEN_FIELDS.includes(f), `${f} must be refused`);
    }
    assert.ok(hasForbiddenField({ name: 'Alex' }));
    assert.ok(hasForbiddenField({ yearsExperience: 7, email: 'a@b.c' }));
    assert.ok(!hasForbiddenField({ yearsExperience: 7, agentUse: 'weekly' }));
});

test('a participant code is long, random, and free of ambiguous letters', () => {
    const a = newParticipantCode();
    const b = newParticipantCode();
    assert.match(a, CODE_PATTERN);
    assert.notEqual(a, b);
    assert.ok(!/[ilo01]/.test(a.slice(2)), 'a code has to be readable over a call');
});
