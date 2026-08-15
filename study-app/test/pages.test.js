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
export const getDoc = async () => ({ exists: () => false, data: () => ({}) });
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
