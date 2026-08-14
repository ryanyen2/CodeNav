// Data comes back out of Firestore and turns into a picture.
//
// This is the end of the first phase: the mirror put a session in, this takes it
// out and draws it with the same chart code the dashboard uses. Between the two,
// the whole path is covered without needing a browser open.
//
//   npm run test:dashboard      (needs the emulator, and data seeded into it)
import test, { before } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { FirestoreRest, emulatorHosts, decodeFields } from '../../docs/study-materials/logger/firestore-rest.js';
import { Mirror } from '../../docs/study-materials/logger/mirror.js';

const PROJECT = 'codoc-study-rules-test';
const CODE = 'p-dashboardtest';
const hosts = emulatorHosts();
const ADMIN = { authorization: 'Bearer owner', 'content-type': 'application/json' };

/**
 * A plausible forty minutes, pushed through the real mirror rather than written
 * straight into the database. That way this covers the path a session actually
 * takes, and it needs no manual setup to run.
 */
async function seed() {
    await fetch(`${hosts.firestore}/projects/${PROJECT}/databases/(default)/documents/participants?documentId=${CODE}`, {
        method: 'POST', headers: ADMIN,
        body: JSON.stringify({ fields: {
            createdAt: { integerValue: String(Date.now()) },
            released: { booleanValue: false },
        } }),
    });

    const ev = [];
    let t = Date.now() - 40 * 60_000;
    const view = (surface, file, secs) => { ev.push({ ev: 'view', surface, file, t: t + secs * 1000, ms: secs * 1000, from: 0, to: 60 }); t += secs * 1000; };
    const edit = (surface, file, n, human = true) => { ev.push({ ev: 'edit', surface, file, t, active: human, focused: true, added: n }); t += 1500; };
    const prompt = (chars) => { ev.push({ ev: 'prompt', t, chars }); t += 2000; };
    const run = (cmd) => { ev.push({ ev: 'agent', t, cmd }); t += 4000; };
    const wait = (secs) => { t += secs * 1000; };

    view('document', 'CLAUDE.md', 95);
    view('code', 'ember/digest.py', 70);
    prompt(180);
    wait(25); edit('code', 'ember/digest.py', 620, false);
    run('pytest');
    wait(140);
    view('document', 'CLAUDE.md', 40);
    edit('document', 'CLAUDE.md', 210);
    view('test', 'tests/test_digest.py', 45);
    run('pytest');
    wait(420);                       // a real break
    prompt(240);
    wait(30); edit('code', 'ember/digest.py', 310, false);
    run('ember');

    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dash-'));
    const logPath = path.join(dir, 'interaction.jsonl');
    fs.writeFileSync(logPath, ev.map((e) => JSON.stringify(e)).join('\n') + '\n');
    const m = new Mirror({
        logPath, code: CODE, condition: 'codoc',
        client: new FirestoreRest({ apiKey: 'emu', projectId: PROJECT, hosts }),
    });
    if (!await m.start()) throw new Error('the mirror could not start against the emulator');
    await m.flush(true);
}

let actions = [];
let timeline; let ribbon; let legend;

before(async () => {
    // d3 reads document at import time, so the DOM has to exist first.
    const dom = new JSDOM('<!doctype html><body><div id="chart"></div><div id="ribbon"></div><div id="legend"></div></body>',
        { pretendToBeVisual: true });
    global.window = dom.window;
    global.document = dom.window.document;
    // navigator is a getter-only global on modern Node, so it is defined rather
    // than assigned.
    Object.defineProperty(global, 'navigator', {
        value: dom.window.navigator, configurable: true, writable: true,
    });
    ({ timeline, ribbon, legend } = await import('../experimenter/charts.js'));

    await seed();
    const url = `${hosts.firestore}/projects/${PROJECT}/databases/(default)/documents/`
        + `participants/${CODE}/sessions/codoc/batches`;
    const r = await fetch(url, { headers: ADMIN });
    const body = await r.json();
    actions = (body.documents || [])
        .map((d) => decodeFields(d.fields))
        .flatMap((d) => d.actions || [])
        .sort((a, b) => a.t - b.t);
});

test('the session that was sent comes back out', () => {
    assert.ok(actions.length > 0, 'no actions came back; seed the emulator first');
    const names = new Set(actions.map((a) => a.a));
    assert.ok(names.has('READ_DOC'), 'reading the description survived the round trip');
    assert.ok(names.has('PROMPT'));
    assert.ok(names.has('AGENT_EDIT'));
    assert.ok(names.has('RUN_TEST'));
    assert.ok(names.has('IDLE'), 'the long break is in the data, not smoothed away');
});

test('what came back is in the closed vocabulary and nothing else', async () => {
    const { ACTIONS } = await import('../shared/actions.js');
    for (const a of actions) {
        assert.ok(ACTIONS.includes(a.a), `${a.a} is not an action`);
    }
});

test('it draws, with one mark per action', () => {
    const el = document.getElementById('chart');
    Object.defineProperty(el, 'clientWidth', { value: 900, configurable: true });
    timeline(el, actions, { animate: false });

    const svg = el.querySelector('svg');
    assert.ok(svg, 'an svg is produced');
    const marks = svg.querySelectorAll('g.mark rect');
    assert.equal(marks.length, actions.length, 'every action is drawn');
    assert.ok(svg.querySelectorAll('.lane-label').length >= 4, 'the lanes are labelled');
    assert.ok(svg.querySelector('.x-axis'), 'time is on an axis');
});

test('every mark is actually visible', () => {
    // The lanes, the axis and the legend can all render correctly while the data
    // itself is invisible, which is exactly what happened: marks began at zero
    // opacity and depended on a transition to appear. A chart that is empty for
    // any reason must fail here rather than look fine.
    const el = document.getElementById('chart');
    timeline(el, actions, { animate: false });
    const rects = [...el.querySelectorAll('g.mark rect')];
    assert.ok(rects.length > 0);
    for (const r of rects) {
        const g = r.parentElement;
        assert.notEqual(g.style.opacity, '0', 'a mark is fully transparent');
        assert.ok(Number(r.getAttribute('width')) > 0, 'a mark has no width');
        assert.ok(Number(r.getAttribute('height')) > 0, 'a mark has no height');
        assert.ok(Number(r.getAttribute('opacity')) > 0.3, 'a mark is nearly invisible');
    }
});

test('a break is drawn as a gap rather than closed up', () => {
    const el = document.getElementById('chart');
    const idleIndex = actions.findIndex((a) => a.a === 'IDLE');
    assert.ok(idleIndex >= 0);
    const rects = [...el.querySelectorAll('g.mark rect')];
    const idleRect = rects[idleIndex];
    // The idle band spans the lanes rather than sitting in one of them.
    assert.ok(Number(idleRect.getAttribute('height')) > 100,
        'the gap is a full-height band, so it reads as time passing');
    assert.ok(Number(idleRect.getAttribute('width')) > 5, 'and it has real width');
});

test('redrawing with more data adds marks instead of starting over', () => {
    const el = document.getElementById('chart');
    const before = el.querySelector('svg');
    const more = [...actions, { t: actions[actions.length - 1].t + 60_000, a: 'RUN_TEST' }];
    timeline(el, more, { animate: false });
    assert.equal(el.querySelector('svg'), before, 'the same svg is updated, not replaced');
    assert.equal(el.querySelectorAll('g.mark rect').length, more.length);
});

test('the sequence renders as readable words', () => {
    const el = document.getElementById('ribbon');
    ribbon(el, actions);
    const words = [...el.querySelectorAll('span')].map((s) => s.textContent);
    assert.ok(words.length > 0);
    assert.ok(words.includes('read doc') || words.includes('read code'),
        `expected readable labels, got ${words.slice(0, 5).join(', ')}`);
});

test('an empty session draws nothing rather than throwing', () => {
    const el = document.getElementById('chart');
    timeline(el, [], { animate: false });
    assert.equal(el.querySelectorAll('g.mark rect').length, 0);
});

test('the legend names every lane', () => {
    const el = document.getElementById('legend');
    legend(el);
    const text = el.textContent;
    for (const lane of ['Description', 'Code', 'Tests', 'Agent', 'Runs']) {
        assert.ok(text.includes(lane), `${lane} is missing from the legend`);
    }
});
