// Load each page the way a browser does, and make it render.
//
// This exists because of a bug that reached production: the dashboard used a
// helper that was only ever defined in the participant page, so the first click
// on New threw "esc is not defined". Every other test passed, because every other
// test exercised a module rather than a page. Nothing had ever loaded the page.
//
// Firebase is replaced with a stub, so this needs no network and no emulator.
// What it checks is the thing unit tests structurally cannot: that the page's own
// code runs at all.
//
//   node --test test/pages.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { build } from 'esbuild';
import { readFileSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

/** A Firebase that does nothing, so the page's own code is what is under test. */
const STUB = `
export const initializeApp = () => ({});
export const getAuth = () => ({ currentUser: { uid: 'u1' } });
export const getFirestore = () => ({});
export const GoogleAuthProvider = class {};
export const signInWithPopup = async () => ({});
export const signInAnonymously = async () => ({});
export const signOut = async () => {};
export const onAuthStateChanged = (a, cb) => { globalThis.__authCb = cb; };
export const connectAuthEmulator = () => {};
export const connectFirestoreEmulator = () => {};
export const collection = (...a) => ({ path: a.slice(1).join('/') });
export const doc = (...a) => ({ path: a.slice(1).join('/') });
export const setDoc = async () => {};
export const deleteDoc = async (ref) => {
  globalThis.__deleted = globalThis.__deleted || [];
  globalThis.__deleted.push((ref && ref.path) || '');
};
export const getDoc = async () => ({ exists: () => false, data: () => ({}) });
// A test can plant collections here, keyed by path, so the results view can be
// driven with real-shaped data rather than only checked when empty.
export const getDocs = async (ref) => ({
  docs: (globalThis.__collections || {})[(ref && ref.path) || ''] || [],
});
export const query = (c) => c;
export const orderBy = () => ({});
export const serverTimestamp = () => 0;
export const onSnapshot = (ref, cb) => {
  globalThis.__snaps = globalThis.__snaps || [];
  globalThis.__snaps.push({ ref, cb });
  return () => {};
};
`;

async function loadPage(page, storage) {
    const dir = mkdtempSync(join(tmpdir(), 'page-'));
    const stub = join(dir, 'firebase-stub.js');
    writeFileSync(stub, STUB);

    const out = join(dir, 'bundle.js');
    await build({
        entryPoints: [join(root, page, 'app.js')],
        bundle: true, format: 'iife', target: 'es2022', outfile: out,
        logLevel: 'silent',
        alias: { 'firebase/app': stub, 'firebase/auth': stub, 'firebase/firestore': stub },
    });

    const html = readFileSync(join(root, page, 'index.html'), 'utf8');
    const dom = new JSDOM(html, {
        url: 'https://example.test/?code=p-abcdefghjkmn&order=codoc-first',
        pretendToBeVisual: true, runScripts: 'outside-only',
    });
    // Seeded before the bundle runs, because the page reads where it got to on
    // start. This is how a test lands on a step other than the first.
    for (const [k, v] of Object.entries(storage || {})) dom.window.localStorage.setItem(k, v);

    const errors = [];
    dom.window.addEventListener('error', (e) => errors.push(e.message));
    dom.window.alert = (m) => errors.push(`alert: ${m}`);
    dom.window.matchMedia = dom.window.matchMedia || (() => ({ matches: false }));

    // Give the page the globals a browser would.
    global.window = dom.window;
    global.document = dom.window.document;
    Object.defineProperty(global, 'navigator', {
        value: dom.window.navigator, configurable: true, writable: true,
    });

    try {
        dom.window.eval(readFileSync(out, 'utf8'));
    } catch (err) {
        errors.push(String(err && err.message));
    }
    return { dom, errors, window: dom.window, document: dom.window.document };
}

// ── the dashboard ────────────────────────────────────────────────────────────

test('the dashboard loads without throwing', async () => {
    const { errors } = await loadPage('experimenter');
    assert.deepEqual(errors, [], errors.join('; '));
});

test('creating a participant does not throw', async () => {
    // The exact click that failed in production. It got as far as writing the
    // participant and then threw while drawing the page, so the record existed
    // and the researcher saw an error.
    const { document, window, errors } = await loadPage('experimenter');

    // Sign in, so the roster renders.
    window.__authCb?.({ email: 'someone@example.com' });
    const snap = (window.__snaps || []).find((s) => s.ref.path === 'participants');
    snap?.cb({
        docs: [{ id: 'p-5zm335hytfs6', data: () => ({ createdAt: 1, order: 'codoc-first' }) }],
    });

    assert.deepEqual(errors, [], errors.join('; '));
    assert.match(document.body.textContent, /p-5zm335hytfs6/, 'the participant is listed');
    // Selecting one renders the whole detail pane, including the forms, which is
    // where the undefined helper was used.
    assert.match(document.body.textContent, /The sign-off/, 'the forms rendered');
    assert.match(document.body.textContent, /Who settled what/);
    assert.match(document.body.textContent, /The questions/);
});

test('the dashboard hands over a link and a command, both carrying the code', async () => {
    // The session cannot start without these two, and before this existed the
    // dashboard showed a code and no way to use it.
    const { document, window } = await loadPage('experimenter');
    window.__authCb?.({ email: 'someone@example.com' });
    (window.__snaps || []).find((s) => s.ref.path === 'participants')?.cb({
        docs: [{ id: 'p-abcdefghjkmn', data: () => ({ createdAt: 1, order: 'baseline-first' }) }],
    });

    const shown = [...document.querySelectorAll('.give-row code')].map((c) => c.textContent);
    assert.equal(shown.length, 2, 'a link and a command');
    assert.match(shown[0], /\/participant\/\?code=p-abcdefghjkmn&order=baseline-first$/);
    // The order has to ride in the link: a participant cannot read their own
    // record, so a bare link would silently run them in the wrong order.
    assert.equal(shown[1], './setup.sh p-abcdefghjkmn baseline-first');
});

test('it says which half of the handoff has not landed', async () => {
    const { document, window } = await loadPage('experimenter');
    window.__authCb?.({ email: 'someone@example.com' });
    (window.__snaps || []).find((s) => s.ref.path === 'participants')?.cb({
        docs: [{ id: 'p-abcdefghjkmn', data: () => ({ createdAt: 1, order: 'codoc-first' }) }],
    });

    const devices = (window.__snaps || [])
        .find((s) => s.ref.path === 'participants/p-abcdefghjkmn/devices');
    assert.ok(devices, 'the dashboard watches the device slots');

    devices.cb({ docs: [{ id: 'browser' }] });
    const text = document.querySelector('#handoff').textContent;
    assert.match(text, /They have opened it/);
    assert.match(text, /editor has not reported/,
        'the expensive failure is the one named');

    devices.cb({ docs: [{ id: 'browser' }, { id: 'mirror' }] });
    const settled = document.querySelector('#handoff').textContent.replace(/\s+/g, ' ');
    assert.match(settled, /page open and their editor is reporting/);
    assert.ok(document.querySelector('#handoff').classList.contains('settled'),
        'it gets out of the way once both have landed');
});

test('the handoff card does not carry a key, and says so', async () => {
    // A website is the wrong place for an API key, and a dashboard field that
    // looked ready to paste one into would invite exactly that.
    const { document, window } = await loadPage('experimenter');
    window.__authCb?.({ email: 'someone@example.com' });
    (window.__snaps || []).find((s) => s.ref.path === 'participants')?.cb({
        docs: [{ id: 'p-abcdefghjkmn', data: () => ({ createdAt: 1, order: 'codoc-first' }) }],
    });

    const card = document.querySelector('#handoff');
    assert.match(card.textContent.replace(/\s+/g, ' '), /two keys, by hand/i);
    // Only the link and the command are copyable. A third copy field would mean
    // somebody had put a key in the page.
    assert.equal(card.querySelectorAll('[data-copy]').length, 2);
    assert.ok(!/sk-ant-|sk-proj-|OPENAI_API_KEY=\S/.test(card.innerHTML),
        'no key or key-shaped value anywhere in the card');
});

test('the participant page warns that setup will ask for the keys', async () => {
    // They run setup alone, days ahead. Being surprised by a prompt for a
    // credential is how somebody pastes the wrong thing or gives up and waits
    // for the call, which costs the session's first twenty minutes.
    const { buildSteps } = await import('../participant/steps.js');
    const at = buildSteps('codoc-first').findIndex((s) => s.kind === 'setup');
    assert.ok(at > 0, 'there is a setup step to land on');

    const { document, errors } = await loadPage('participant', {
        'codoc-study:p-abcdefghjkmn': JSON.stringify({ at, answers: {} }),
    });
    await new Promise((r) => setTimeout(r, 30));

    assert.deepEqual(errors, [], errors.join('; '));
    const text = document.body.textContent.replace(/\s+/g, ' ');
    assert.match(text, /Set up your machine/, 'we are on the setup step');
    assert.match(text, /ask you for two keys/);
    assert.match(text, /not shown as you type/, 'so they do not share a key on screen');
    assert.match(text, /we pay for them/, 'nobody should think this costs them');
    // The command itself must still carry their own code, not the example.
    assert.match(text, /\.\/setup\.sh p-abcdefghjkmn codoc-first/);
});

test('the forms show the questions for the right project', async () => {
    const { document, window } = await loadPage('experimenter');
    window.__authCb?.({ email: 'someone@example.com' });
    (window.__snaps || []).find((s) => s.ref.path === 'participants')?.cb({
        docs: [{ id: 'p-x', data: () => ({ createdAt: 1, order: 'codoc-first' }) }],
    });
    // codoc-first means the codoc condition is hearth, so the hearth decisions
    // are the ones on screen.
    assert.match(document.body.textContent, /What marks a post as a draft/);
    assert.ok(!document.body.textContent.includes('Where a mute is configured'));
});

// ── the participant page ─────────────────────────────────────────────────────

test('the participant page loads without throwing', async () => {
    const { errors } = await loadPage('participant');
    assert.deepEqual(errors, [], errors.join('; '));
});

test('it opens on the welcome step with a code in the link', async () => {
    const { document, errors } = await loadPage('participant');
    await new Promise((r) => setTimeout(r, 30));   // it signs in before rendering
    assert.deepEqual(errors, [], errors.join('; '));
    assert.match(document.body.textContent, /Thanks for taking part/);
});

test('a link with no code says so rather than failing quietly', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'page-'));
    const stub = join(dir, 'firebase-stub.js');
    writeFileSync(stub, STUB);
    const out = join(dir, 'bundle.js');
    await build({
        entryPoints: [join(root, 'participant', 'app.js')],
        bundle: true, format: 'iife', target: 'es2022', outfile: out, logLevel: 'silent',
        alias: { 'firebase/app': stub, 'firebase/auth': stub, 'firebase/firestore': stub },
    });
    const dom = new JSDOM(readFileSync(join(root, 'participant', 'index.html'), 'utf8'),
        { url: 'https://example.test/', pretendToBeVisual: true, runScripts: 'outside-only' });
    global.window = dom.window;
    global.document = dom.window.document;
    dom.window.eval(readFileSync(out, 'utf8'));
    await new Promise((r) => setTimeout(r, 30));
    assert.match(dom.window.document.body.textContent, /missing its code/);
});

// ── the cohort roster ────────────────────────────────────────────────────────

test('the roster shows the whole plan, not only who exists', async () => {
    // "How many more do I need, in which order" is the mid-study question, and a
    // list of only what exists cannot answer it.
    const { document, window } = await loadPage('experimenter');
    window.__authCb?.({ email: 'someone@example.com' });
    (window.__snaps || []).find((s) => s.ref.path === 'participants')?.cb({ docs: [] });

    const list = document.querySelector('#roster-list');
    assert.match(list.textContent, /0 of 2 pilots/);
    assert.match(list.textContent, /0 of 12 participants/);
    assert.equal(list.querySelectorAll('.p-item.open').length, 14,
        'every unfilled slot is drawn');
    assert.match(list.textContent, /Pilots/);
    assert.match(list.textContent, /P01/);
});

test('a created person takes their slot and the count moves', async () => {
    const { document, window } = await loadPage('experimenter');
    window.__authCb?.({ email: 'someone@example.com' });
    (window.__snaps || []).find((s) => s.ref.path === 'participants')?.cb({
        docs: [
            { id: 'p-aaaaaaaaaaaa', data: () => ({ createdAt: 1, order: 'codoc-first', pilot: true }) },
            { id: 'p-bbbbbbbbbbbb', data: () => ({ createdAt: 2, order: 'codoc-first' }) },
        ],
    });
    const list = document.querySelector('#roster-list');
    assert.match(list.textContent, /1 of 2 pilots/);
    assert.match(list.textContent, /1 of 12 participants/);
    assert.equal(list.querySelectorAll('.p-item.open').length, 12);
    assert.match(list.textContent, /2 analysable|1 analysable/);
});

test('an excluded person is counted as run but not as analysable', async () => {
    const { document, window } = await loadPage('experimenter');
    window.__authCb?.({ email: 'someone@example.com' });
    (window.__snaps || []).find((s) => s.ref.path === 'participants')?.cb({
        docs: [
            { id: 'p-cccccccccccc', data: () => ({ createdAt: 1, order: 'codoc-first' }) },
            { id: 'p-dddddddddddd', data: () => ({ createdAt: 2, order: 'baseline-first', excluded: true }) },
        ],
    });
    const list = document.querySelector('#roster-list');
    assert.match(list.textContent, /2 of 12 participants/, 'both sessions happened');
    assert.match(list.textContent, /1 analysable, 1 excluded/);
});

// ── the results view ─────────────────────────────────────────────────────────

test('results draws every figure, and says so when there is nothing yet', async () => {
    const { document, window } = await loadPage('experimenter');
    window.__authCb?.({ email: 'someone@example.com' });
    (window.__snaps || []).find((s) => s.ref.path === 'participants')?.cb({ docs: [] });

    document.querySelector('#show-results').click();
    await new Promise((r) => setTimeout(r, 60));

    // With no sessions it must say so rather than showing four empty frames,
    // which read as broken rather than as early.
    assert.match(document.querySelector('#detail').textContent, /Nothing to draw yet/);
    assert.match(document.querySelector('#detail').textContent, /Pilots are out/,
        'the default is stated, because it changes what the numbers mean');
});

test('results draws all four figures once sessions exist', async () => {
    const { document, window } = await loadPage('experimenter');
    const acts = (pool, n) => Array.from({ length: n }, (_, i) => ({
        action: pool[i % pool.length], t: 1_700_000_000_000 + i * 9000,
        ms: i % 3 === 0 ? 8000 : 0,
    }));
    window.__collections = {
        'participants/p-eeeeeeeeeeee/sessions/codoc/batches': [
            { data: () => ({ seq: 0, actions: acts(['READ_DOC', 'PROMPT', 'AGENT_EDIT', 'EDIT_DOC', 'RUN_TEST'], 60) }) },
        ],
        'participants/p-eeeeeeeeeeee/sessions/baseline/batches': [
            { data: () => ({ seq: 0, actions: acts(['READ_CODE', 'PROMPT', 'AGENT_EDIT', 'RUN_TEST'], 60) }) },
        ],
        'participants/p-eeeeeeeeeeee/answers': [
            { id: 'after-codoc', data: () => ({ doc1: 6, umux1: 5, tlxMental: 3 }) },
            { id: 'after-baseline', data: () => ({ doc1: 2, umux1: 4, tlxMental: 5 }) },
        ],
    };
    window.__authCb?.({ email: 'someone@example.com' });
    (window.__snaps || []).find((s) => s.ref.path === 'participants')?.cb({
        docs: [{ id: 'p-eeeeeeeeeeee', data: () => ({ createdAt: 1, order: 'codoc-first' }) }],
    });

    document.querySelector('#show-results').click();
    await new Promise((r) => setTimeout(r, 120));

    const detail = document.querySelector('#detail');
    assert.equal(detail.querySelectorAll('.fig').length, 4, 'four figures');
    // Each one drew something, rather than showing its own failure notice.
    for (const card of detail.querySelectorAll('.fig')) {
        const svg = card.querySelector('.fig-holder svg');
        assert.ok(svg, `${card.dataset.fig} drew nothing: ${card.textContent.slice(0, 120)}`);
        assert.ok(svg.querySelectorAll('*').length > 5, `${card.dataset.fig} is empty`);
    }
    // And every one offers its numbers, not only the picture.
    assert.equal(detail.querySelectorAll('[data-dl="csv"]').length, 4);
});

test('a pilot is left out of the figures unless asked for', async () => {
    // A pilot exists to find out the instrument is broken. Analysing it quietly
    // removes the only way left to say so.
    const { document, window } = await loadPage('experimenter');
    window.__collections = {
        'participants/p-ffffffffffff/sessions/codoc/batches': [
            { data: () => ({ seq: 0, actions: Array.from({ length: 40 }, (_, i) =>
                ({ action: 'READ_DOC', t: 1_700_000_000_000 + i * 9000, ms: 5000 })) }) },
        ],
    };
    window.__authCb?.({ email: 'someone@example.com' });
    (window.__snaps || []).find((s) => s.ref.path === 'participants')?.cb({
        docs: [{ id: 'p-ffffffffffff', data: () => ({ createdAt: 1, order: 'codoc-first', pilot: true }) }],
    });

    document.querySelector('#show-results').click();
    await new Promise((r) => setTimeout(r, 120));
    assert.match(document.querySelector('#detail').textContent, /Nothing to draw yet/,
        'the only session is a pilot, so there is nothing to report');

    document.querySelector('#inc-pilots').click();
    await new Promise((r) => setTimeout(r, 60));
    assert.equal(document.querySelectorAll('#detail .fig').length, 4,
        'and it comes back when explicitly asked for');
});

test('a code can be released when a participant changes machine', async () => {
    // The device slot is claimed once, which is what stops a stray copy of the
    // code writing into a session. The pilot found the other side of that: a
    // participant who reinstalls is locked out, their editor tells them to ask
    // the experimenter, and the experimenter had no way to do it.
    const { document, window } = await loadPage('experimenter');
    window.confirm = () => true;
    window.__deleted = [];
    window.__authCb?.({ email: 'someone@example.com' });
    (window.__snaps || []).find((s) => s.ref.path === 'participants')?.cb({
        docs: [{ id: 'p-abcdefghjkmn', data: () => ({ createdAt: 1, order: 'codoc-first' }) }],
    });
    const devices = (window.__snaps || [])
        .find((s) => s.ref.path === 'participants/p-abcdefghjkmn/devices');
    devices.cb({ docs: [{ id: 'browser' }, { id: 'mirror' }] });

    // Only offered once something is actually holding a slot.
    document.querySelector('#handoff-more').click();
    const release = document.querySelector('#release');
    assert.ok(release, 'the release is on the handoff card');
    release.click();
    await new Promise((r) => setTimeout(r, 20));

    assert.deepEqual(window.__deleted.sort(), [
        'participants/p-abcdefghjkmn/devices/browser',
        'participants/p-abcdefghjkmn/devices/mirror',
    ], 'both slots freed, so their software can register again');
});

test('there is nothing to release before anything has registered', async () => {
    const { document, window } = await loadPage('experimenter');
    window.__authCb?.({ email: 'someone@example.com' });
    (window.__snaps || []).find((s) => s.ref.path === 'participants')?.cb({
        docs: [{ id: 'p-abcdefghjkmn', data: () => ({ createdAt: 1, order: 'codoc-first' }) }],
    });
    assert.equal(document.querySelector('#release'), null,
        'offering it would suggest something is wrong when nothing is');
});
