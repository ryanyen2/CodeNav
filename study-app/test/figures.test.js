// The figures: that they render, that they stand on their own, and that the
// numbers behind them are the ones we think.
//
// Set FIGURE_OUT to also write the SVGs somewhere and look at them:
//   FIGURE_OUT=/tmp/figs node --test test/figures.test.js
import test, { before } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';

let likert; let tally; let timeShare; let timeProfile;
let authorship; let provenance; let transitionLift; let mediation; let TRANSITIONS;
let toCsv; let serialize; let toSequence;

const OUT = process.env.FIGURE_OUT;

before(async () => {
    const dom = new JSDOM('<!doctype html><body></body>', { pretendToBeVisual: true });
    global.window = dom.window;
    global.document = dom.window.document;
    global.XMLSerializer = dom.window.XMLSerializer;
    ({ likert, tally } = await import('../figures/likert.js'));
    ({ timeShare, timeProfile } = await import('../figures/timeprofile.js'));
    ({ authorship, provenance } = await import('../figures/provenance.js'));
    ({ transitionLift, mediation, TRANSITIONS } = await import('../figures/mediation.js'));
    ({ toCsv, serialize } = await import('../figures/export.js'));
    ({ toSequence } = await import('../../docs/study-materials/logger/actions-vocab.js'));
    if (OUT) mkdirSync(OUT, { recursive: true });
});

function save(name, node) {
    if (!OUT) return;
    writeFileSync(join(OUT, `${name}.svg`), serialize(node));
}

/**
 * A session, built by running raw logger events through the REAL sequence
 * builder rather than by writing actions out directly.
 *
 * This is not fastidiousness. Every figure originally read `a.action`; the logger
 * emits `a.a`. Sixteen tests passed because the fixtures agreed with the figures
 * and neither agreed with the logger, and the whole set would have drawn nothing
 * from a real session. Fixtures that come from the source of truth cannot drift
 * from it again.
 */
function fakeSession(code, condition, seed = 1) {
    let s = seed;
    const rnd = () => { s = (s * 1103515245 + 12345) % 2147483648; return s / 2147483648; };
    const doc = condition === 'codoc' ? '.codoc/tree.codoc' : 'CLAUDE.md';
    const pool = condition === 'codoc'
        ? [[doc, 'document', 'view'], ['scribe/notes.py', 'code', 'view'],
           [doc, 'document', 'human'], [null, null, 'prompt'],
           ['scribe/notes.py', 'code', 'agent'], [doc, 'document', 'agent'],
           [null, null, 'pytest'], [null, null, 'accept']]
        : [[doc, 'document', 'view'], ['scribe/notes.py', 'code', 'view'],
           ['scribe/notes.py', 'code', 'human'], [null, null, 'prompt'],
           ['scribe/notes.py', 'code', 'agent'], [null, null, 'pytest']];
    const raw = [];
    let t = 1_700_000_000_000;
    for (let i = 0; i < 120; i += 1) {
        const [file, surface, kind] = pool[Math.floor(rnd() * pool.length)];
        if (kind === 'view') {
            const ms = 4000 + Math.floor(rnd() * 20000);
            t += ms;
            raw.push({ t, ev: 'view', surface, file, ms });
        } else if (kind === 'human' || kind === 'agent') {
            t += 4000;
            raw.push({ t, ev: 'edit', surface, file, added: 20,
                active: kind === 'human', focused: kind === 'human' });
        } else if (kind === 'prompt') {
            t += 20000; raw.push({ t, ev: 'prompt', chars: 140 });
        } else if (kind === 'pytest') {
            t += 9000; raw.push({ t, ev: 'agent', cmd: 'pytest' });
        } else {
            t += 3000; raw.push({ t, ev: 'verdict', kind: 'accept' });
        }
    }
    return { code, condition, actions: toSequence(raw) };
}

// Built on first use, not at import: the fixtures now run through the real
// sequence builder, which is only loaded once the DOM exists.
let _cohort = null;
function cohort() {
    if (!_cohort) {
        _cohort = [
            ...Array.from({ length: 6 }, (_, i) => fakeSession(`p-${i}`, 'codoc', i + 1)),
            ...Array.from({ length: 6 }, (_, i) => fakeSession(`p-${i}`, 'baseline', i + 20)),
        ];
    }
    return _cohort;
}

// ── every figure renders and carries its own styling ─────────────────────────

const ITEMS = [
    { id: 'doc1', text: 'The description matched the code.' },
    { id: 'doc2', text: 'Keeping it current felt like busywork.', reverse: true },
    { id: 'rev1', text: 'I was confident the code was correct.' },
];

/** Per-participant ratings, which is what pairing needs. */
const RATINGS = Array.from({ length: 8 }, (_, p) => ITEMS.flatMap((it) => [
    { code: `p-${p}`, condition: 'codoc', item: it.id, value: 4 + (p % 4) },
    { code: `p-${p}`, condition: 'baseline', item: it.id, value: 2 + (p % 3) },
])).flat();

function makeAll() {
    const counts = {
        codoc: tally([{ doc1: 6, doc2: 2, rev1: 5 }, { doc1: 7, doc2: 3, rev1: 6 },
            { doc1: 5, doc2: 2, rev1: 4 }], ITEMS).counts,
        baseline: tally([{ doc1: 3, doc2: 5, rev1: 4 }, { doc1: 2, doc2: 6, rev1: 3 },
            { doc1: 4, doc2: 4, rev1: 5 }], ITEMS).counts,
    };
    const byCondition = {};
    for (const c of ['codoc', 'baseline']) {
        byCondition[c] = transitionLift(cohort().filter((s) => s.condition === c));
    }
    return {
        likert: likert({ items: ITEMS, conditions: ['codoc', 'baseline'], counts, points: 7,
            ratings: RATINGS }),
        timeprofile: timeProfile(['codoc', 'baseline'].map((c) => ({
            condition: c,
            n: cohort().filter((s) => s.condition === c).length,
            medianMinutes: 42,
            profile: timeShare(cohort().filter((s) => s.condition === c)),
        }))),
        provenance: provenance(authorship(cohort())),
        mediation: mediation(byCondition),
    };
}

test('every figure renders to an svg with a size', () => {
    for (const [name, node] of Object.entries(makeAll())) {
        assert.equal(node.tagName, 'svg', name);
        assert.ok(Number(node.getAttribute('width')) > 100, `${name} has no width`);
        assert.ok(Number(node.getAttribute('height')) > 40, `${name} has no height`);
        assert.ok(node.querySelectorAll('*').length > 5, `${name} is empty`);
        save(name, node);
    }
});

test('nothing depends on a stylesheet, so the file stands alone in LaTeX', () => {
    // The whole reason the figures set attributes rather than classes. A `class`
    // or a `style` here means the exported file looks different in the paper
    // than it did on the dashboard, and nobody would notice until proofs.
    for (const [name, node] of Object.entries(makeAll())) {
        assert.equal(node.querySelectorAll('[class]').length, 0, `${name} uses a class`);
        assert.equal(node.querySelectorAll('[style]').length, 0, `${name} uses inline style`);
        for (const t of node.querySelectorAll('text')) {
            assert.ok(t.getAttribute('font-family'), `${name} has text with no font set`);
        }
    }
});

test('text stays text, so it is selectable in the PDF', () => {
    const node = makeAll().likert;
    assert.ok(node.querySelectorAll('text').length > 5);
    assert.equal(node.querySelectorAll('image').length, 0, 'nothing rasterized');
});

test('a reverse keyed item is marked on the figure', () => {
    // Without the mark, a reader comparing rows takes a low bar as a bad result
    // on every row, including the ones where low is the good direction.
    const node = makeAll().likert;
    const marks = [...node.querySelectorAll('text')].filter((t) => t.textContent === 'R');
    assert.equal(marks.length, 1, 'exactly the one reverse keyed item is marked');
});

// ── the numbers ──────────────────────────────────────────────────────────────

test('both Likert panels share one scale', () => {
    // Scaling each panel to its own maximum would make a condition with fewer
    // answers look the same as a full one, which is the classic way to make a
    // small n disappear.
    const counts = {
        codoc: tally([{ doc1: 7 }, { doc1: 7 }, { doc1: 7 }], ITEMS).counts,
        baseline: tally([{ doc1: 7 }], ITEMS).counts,
    };
    const node = likert({ items: ITEMS, conditions: ['codoc', 'baseline'], counts, points: 7 });
    const widths = [...node.querySelectorAll('rect')]
        .map((r) => Number(r.getAttribute('width')))
        .filter((w) => w > 0 && w < 200);
    const wide = Math.max(...widths);
    const narrow = Math.min(...widths.filter((w) => w > 1));
    assert.ok(wide > narrow * 2, 'one answer must not fill the panel like three do');
});

test('an answer off the scale is counted, not quietly dropped', () => {
    // The workload block is answered on 0–100 in fives and is reported as a
    // score rather than drawn here. If it is ever handed to this figure anyway,
    // every bar would come out empty and the picture would look merely sparse.
    const seven = tally([{ doc1: 7 }, { doc1: 55 }, { doc1: 4 }], ITEMS);
    assert.equal(seven.offScale, 1);
    assert.equal(seven.counts.doc1[6], 1, '7 lands on the last of seven points');

    const tlx = tally([{ doc1: 55 }, { doc1: 0 }], ITEMS, { min: 0, max: 100, step: 5 });
    assert.equal(tlx.offScale, 0);
    assert.equal(tlx.counts.doc1.length, 21);
    assert.equal(tlx.counts.doc1[11], 1, '55 is the twelfth of twenty-one marks');
});

test('the time profile sums to one in every slice', () => {
    const p = timeShare(cohort().filter((s) => s.condition === 'codoc'));
    for (let b = 0; b < p.bins; b += 1) {
        const total = p.actions.reduce((a, k) => a + p.share[k][b], 0);
        assert.ok(Math.abs(total - 1) < 1e-9, `slice ${b} sums to ${total}`);
    }
});

test('a long session does not outweigh a short one', () => {
    // Per session rather than per action, or one person who worked for ninety
    // minutes becomes the finding.
    const short = { condition: 'codoc', actions: [
        { a: 'READ_DOC', t: 0, ms: 1000 }, { a: 'PROMPT', t: 1000, ms: 0 }] };
    const long = { condition: 'codoc', actions: Array.from({ length: 200 }, (_, i) =>
        ({ a: 'READ_CODE', t: i * 1000, ms: 1000 })) };
    const p = timeShare([short, long]);
    const docShare = p.share.READ_DOC ? p.share.READ_DOC[0] : 0;
    assert.ok(docShare > 0.2,
        `the short session should still be about half of the first slice, got ${docShare}`);
});

test('a description nobody wrote to has no author split', () => {
    // Plotting it at the midpoint would invent a balanced authorship out of
    // nothing, which is the exact claim the figure exists to test.
    const rows = authorship([{ code: 'p-1', condition: 'baseline', actions: [
        { a: 'READ_CODE', t: 1 }, { a: 'PROMPT', t: 2 }] }]);
    assert.equal(rows[0].docWrites, 0);
    assert.equal(rows[0].humanShareOfDoc, null);
});

test('authorship counts the person and the agent separately', () => {
    const rows = authorship([{ code: 'p-1', condition: 'codoc', actions: [
        { a: 'EDIT_DOC', t: 1 }, { a: 'EDIT_DOC', t: 2 }, { a: 'AGENT_DOC', t: 3 },
        { a: 'EDIT_CODE', t: 4 }, { a: 'AGENT_EDIT', t: 5 }] }]);
    assert.deepEqual(
        [rows[0].humanDoc, rows[0].agentDoc, rows[0].humanCode, rows[0].agentCode],
        [2, 1, 1, 1]);
    assert.equal(rows[0].humanShareOfDoc, 2 / 3);
});

test('lift is above zero only when a pair beats its own parts', () => {
    // A sequence where READ_DOC always precedes PROMPT, against one where the
    // two never touch. Same counts of each action in both.
    const always = { condition: 'codoc', actions: 'READ_DOC PROMPT READ_DOC PROMPT READ_DOC PROMPT READ_DOC PROMPT'
        .split(' ').map((a, i) => ({ a, t: i * 1000 })) };
    const never = { condition: 'codoc', actions: 'READ_DOC READ_DOC READ_DOC READ_DOC PROMPT PROMPT PROMPT PROMPT'
        .split(' ').map((a, i) => ({ a, t: i * 1000 })) };
    const [a] = transitionLift([always], [TRANSITIONS[0]]);
    const [n] = transitionLift([never], [TRANSITIONS[0]]);
    assert.ok(a.lift > 0, `paired should be positive, got ${a.lift}`);
    assert.ok(n.lift < a.lift, 'separated must score below paired');
});

test('a transition whose parts never happened contributes nothing', () => {
    // Otherwise a condition that cannot produce an action at all would be
    // scored as strongly avoiding it, which is an artifact, not a behaviour.
    const s = { condition: 'baseline', actions: 'READ_CODE PROMPT READ_CODE PROMPT'
        .split(' ').map((a, i) => ({ a, t: i * 1000 })) };
    const [r] = transitionLift([s], [{ from: 'EDIT_DOC', to: 'PROMPT', label: 'x' }]);
    assert.equal(r.n, 0);
    assert.equal(r.lift, null);
});

test('idle does not break a pair in half', () => {
    const s = { condition: 'codoc', actions: [
        { a: 'READ_DOC', t: 0 }, { a: 'IDLE', t: 1 }, { a: 'PROMPT', t: 2 },
        { a: 'READ_DOC', t: 3 }, { a: 'PROMPT', t: 4 }] };
    const [r] = transitionLift([s], [TRANSITIONS[0]]);
    assert.equal(r.obs, 2, 'a gap between two moves does not break the relationship');
});

test('the transitions are fixed in advance, not mined', () => {
    // Mining a dozen sessions for the best bigram and reporting it is how a
    // study finds something that will not replicate.
    assert.ok(TRANSITIONS.length >= 5);
    for (const t of TRANSITIONS) {
        assert.ok(t.from && t.to && t.label && t.reading,
            `${t.from}->${t.to} has no stated reading, so nobody decided what it would mean`);
    }
});

test('every transition is one both conditions could produce', () => {
    // A comparison that includes ACCEPT would show codoc winning at a thing the
    // baseline has no button for.
    const codocOnly = new Set(['ACCEPT', 'REJECT']);
    for (const t of TRANSITIONS) {
        assert.ok(!codocOnly.has(t.from) && !codocOnly.has(t.to),
            `${t.from}->${t.to} cannot happen in the baseline`);
    }
});

test('the numbers come out as csv beside the figure', () => {
    const csv = toCsv([{ item: 'doc1', condition: 'codoc', n: 3 },
        { item: 'doc1', condition: 'baseline', n: 3 }]);
    assert.match(csv, /^item,condition,n\n/);
    assert.equal(csv.trim().split('\n').length, 3);
    assert.match(toCsv([{ a: 'has, comma' }]), /"has, comma"/);
});

test('the exported file parses as XML', () => {
    // It did not. svg() set an xmlns attribute and the serializer emitted one
    // too, so every figure came out with "Attribute xmlns redefined" and no
    // viewer would open it. The unit tests all passed, because none of them had
    // ever serialized a figure.
    for (const [name, node] of Object.entries(makeAll())) {
        const out = serialize(node);
        assert.equal((out.match(/xmlns=/g) || []).length, 1, `${name} declares xmlns twice`);
        const doc = new global.window.DOMParser().parseFromString(out, 'image/svg+xml');
        assert.equal(doc.querySelector('parsererror'), null, `${name} does not parse`);
        assert.equal(doc.documentElement.tagName, 'svg');
    }
});

test('no row of the provenance figure writes into the next one', () => {
    // The tick labels sat below their own row and landed in the band beneath,
    // which is only visible once the thing is rendered.
    const node = provenance(authorship(cohort()));
    const rows = [...node.querySelectorAll('rect')]
        .filter((r) => r.getAttribute('fill') === '#f2f4f6')
        .map((r) => ({ y: Number(r.getAttribute('y')), h: Number(r.getAttribute('height')) }))
        .sort((a, b) => a.y - b.y);
    assert.ok(rows.length >= 2, 'there are banded rows to check');
    for (const t of node.querySelectorAll('text')) {
        const ty = Number(t.getAttribute('y'));
        if (!Number.isFinite(ty)) continue;
        // Every label belongs to exactly one band, or sits clear of all of them.
        const inside = rows.filter((r) => ty > r.y && ty < r.y + r.h);
        assert.ok(inside.length <= 1, `"${t.textContent}" spans two rows`);
    }
});

test('the figures read the field the logger actually writes', () => {
    // The bug this exists for: every figure read `a.action`, the logger emits
    // `a.a`, and the whole set would have drawn nothing from a real session while
    // passing sixteen tests against fixtures that agreed with the figures.
    const raw = [
        { t: 1000, ev: 'view', surface: 'document', file: 'CLAUDE.md', ms: 1000 },
        { t: 5000, ev: 'prompt', chars: 100 },
    ];
    const seq = toSequence(raw);
    assert.ok(seq.every((x) => typeof x.a === 'string'),
        'the logger writes the action under `a`');
    assert.ok(seq.every((x) => x.action === undefined),
        'and writes nothing under `action`, so nothing may read it');

    // And the analysis actually sees it.
    const rows = authorship([{ code: 'p-1', condition: 'codoc', actions: toSequence([
        { t: 1, ev: 'edit', surface: 'document', file: 'CLAUDE.md', added: 5, active: true, focused: true },
    ]) }]);
    assert.equal(rows[0].humanDoc, 1, 'authorship counts a real logger event');

    const share = timeShare([{ actions: seq }]);
    assert.ok(share.actions.length > 0, 'the time profile sees real logger events');
});

test('a consultation separated by one move still counts as a consultation', () => {
    // The pilot found this: a session that read the description before every
    // instruction scored as AVOIDING that pattern, because what it did was read,
    // write, then instruct, and the strictly adjacent pair never happened.
    const round = 'READ_DOC EDIT_DOC PROMPT AGENT_EDIT RUN_TEST'.split(' ');
    const s = { condition: 'codoc', actions: Array.from({ length: 5 })
        .flatMap(() => round).map((a, i) => ({ a, t: i * 1000 })) };

    const consult = TRANSITIONS.find((t) => t.from === 'READ_DOC' && t.to === 'PROMPT');
    const [windowed] = transitionLift([s], [consult]);
    assert.ok(windowed.lift > 0,
        `reading before every instruction should score positive, got ${windowed.lift}`);

    const [strict] = transitionLift([s], [{ ...consult, within: 1 }]);
    assert.ok(strict.lift < 0, 'and strict adjacency is what got it wrong');
});

test('a window counts one occurrence per opportunity, not one per hit', () => {
    // Otherwise a burst of agent edits scores as several separate checks of what
    // is really one change.
    const s = { condition: 'codoc', actions: 'PROMPT AGENT_EDIT AGENT_EDIT AGENT_EDIT'
        .split(' ').map((a, i) => ({ a, t: i * 1000 })) };
    const [r] = transitionLift([s], [{ from: 'PROMPT', to: 'AGENT_EDIT', within: 3, label: 'x' }]);
    assert.equal(r.obs, 1, 'one prompt, one opportunity, one count');
});

test('every transition says how far ahead it looked', () => {
    // A reader cannot tell an adjacent pair from a windowed one otherwise, and
    // they mean different things.
    for (const t of TRANSITIONS) {
        assert.ok(Number.isInteger(t.within) && t.within >= 1, `${t.label} has no window`);
        assert.ok(t.within <= 3, `${t.label} looks too far ahead to mean "then"`);
    }
    const node = mediation({ codoc: transitionLift(cohort().filter((s) => s.condition === 'codoc')),
        baseline: transitionLift(cohort().filter((s) => s.condition === 'baseline')) });
    const marks = [...node.querySelectorAll('text')].filter((t) => /^≤\d$/.test(t.textContent));
    assert.equal(marks.length, TRANSITIONS.length, 'the window is on the figure, per row');
});
