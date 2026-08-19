// Render each participant step to a standalone HTML file.
//
// The page cannot be looked at without a code, a Firestore project and somebody
// clicking through fifteen steps, so a change to what it SAYS was reviewed by
// reading source. This renders every step the way a participant sees it, into
// `preview/`, where each file opens on its own and screenshots cleanly.
//
//   node scripts/preview.mjs [codoc-first|baseline-first]

import { build } from 'esbuild';
import { JSDOM } from 'jsdom';
import { readFileSync, writeFileSync, mkdirSync, rmSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const order = process.argv[2] || 'codoc-first';
const out = join(root, 'preview');

// The page talks to Firestore on load. Everything it does there fails softly
// already, because a session has to survive a website being down, so a stub that
// throws is the same path a real outage takes.
const stubDir = join(tmpdir(), `codoc-preview-${process.pid}`);
mkdirSync(stubDir, { recursive: true });
const stub = join(stubDir, 'stub.js');
writeFileSync(stub, `
export const initializeApp = () => ({});
export const getAuth = () => ({ currentUser: { uid: 'preview' } });
export const signInAnonymously = async () => { throw new Error('offline'); };
export const connectAuthEmulator = () => {};
export const getFirestore = () => ({});
export const connectFirestoreEmulator = () => {};
export const doc = (...p) => ({ path: p.slice(1).join('/') });
export const getDoc = async () => { throw new Error('denied'); };
export const setDoc = async () => { throw new Error('offline'); };
export const serverTimestamp = () => 0;
`);

const bundle = join(stubDir, 'bundle.js');
await build({
    entryPoints: [join(root, 'participant', 'app.js')],
    bundle: true, format: 'iife', target: 'es2022', outfile: bundle,
    logLevel: 'silent',
    alias: { 'firebase/app': stub, 'firebase/auth': stub, 'firebase/firestore': stub },
});

const { buildSteps } = await import(join(root, 'participant', 'steps.js'));
const steps = buildSteps(order);
const css = readFileSync(join(root, 'participant', 'style.css'), 'utf8');
const html = readFileSync(join(root, 'participant', 'index.html'), 'utf8');

rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

const code = 'pilot-abcdefghjkmn';
const shown = [];

for (const [at, step] of steps.entries()) {
    const dom = new JSDOM(html, {
        url: `https://example.test/?code=${code}&order=${order}`,
        pretendToBeVisual: true, runScripts: 'outside-only',
    });
    dom.window.localStorage.setItem(`codoc-study:${code}`,
        JSON.stringify({ at, answers: {} }));
    dom.window.scrollTo = () => {};
    dom.window.matchMedia = dom.window.matchMedia || (() => ({ matches: false }));
    global.window = dom.window;
    global.document = dom.window.document;
    Object.defineProperty(global, 'navigator',
        { value: dom.window.navigator, configurable: true, writable: true });
    try {
        dom.window.eval(readFileSync(bundle, 'utf8'));
    } catch (err) {
        console.warn(`  ${step.id}: ${err.message}`);
    }
    await new Promise((r) => setTimeout(r, 20));

    const doc = dom.window.document;
    // The pilot bar is a control for us, not part of what a participant reads.
    doc.querySelector('#pilot-bar')?.remove();
    // Pictures are referenced from the page's own folder, which is one level up
    // from where these files are written.
    for (const img of doc.querySelectorAll('img[src^="img/"]')) {
        img.setAttribute('src', `../participant/${img.getAttribute('src')}`);
    }
    for (const link of doc.querySelectorAll('link[rel="stylesheet"]')) link.remove();
    const style = doc.createElement('style');
    style.textContent = css;
    doc.head.append(style);
    doc.querySelector('script')?.remove();

    const name = `${String(at).padStart(2, '0')}-${step.id}.html`;
    writeFileSync(join(out, name), dom.serialize());
    shown.push(name);
    dom.window.close();
}

writeFileSync(join(out, 'index.html'),
    `<!doctype html><meta charset="utf-8"><title>Participant page, ${order}</title>
     <style>body{font:15px/1.6 system-ui;margin:40px;max-width:40em}
     li{margin:4px 0}</style>
     <h1>Participant page, ${order}</h1>
     <ol>${shown.map((f) => `<li><a href="${f}">${f.replace('.html', '')}</a></li>`).join('')}</ol>`);

rmSync(stubDir, { recursive: true, force: true });
console.log(`${shown.length} steps written to ${out}`);
