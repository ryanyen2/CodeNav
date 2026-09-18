/**
 * app.ts — the webview: a tree pane, a toolbar, the editor (09-editor §7–8).
 *
 * The shell is built once from the first projection; every later projection
 * is adopted per block by the editor and re-renders the tree rows and the
 * toolbar counts. The host speaks `HostToWebview` / `WebviewToHost`
 * (host/provider.ts); on the hub the same messages travel over HTTP + SSE.
 */
import './app.css';

import { PHASE_CHANNEL, PHASE_LABEL } from '../calc/claims';
import type { Ack, Phase, Projection, Submission } from '../calc/projection';
import type { HostToWebview, WebviewToHost } from '../host/provider';
import { nextUndo, type HistoryWindow } from '../calc/window';
import { Margin, type Thread } from './comments';
import { mountEditor, type EditorHandle } from './editor';
import { Timeline } from './timeline';

interface VsCodeApi { postMessage(m: unknown): void; getState(): unknown; setState(s: unknown): void }
declare function acquireVsCodeApi(): VsCodeApi;

const api: VsCodeApi = typeof acquireVsCodeApi === 'function'
    ? acquireVsCodeApi()
    : { postMessage: () => undefined, getState: () => undefined, setState: () => undefined };

const send = (m: WebviewToHost) => api.postMessage(m);

const root = document.getElementById('app')!;
let editor: EditorHandle | null = null;
let margin: Margin | null = null;
let timeline: Timeline | null = null;
let actor = 'human:local';
let pendingUndo: 'undo' | 'redo' | null = null;
/** Batch → the client ids it submitted that the daemon has not answered. Undo
 *  waits for these, because it must choose from a window that already carries
 *  the person's newest work. A batch is retired by its ACKS, not by the window:
 *  the window drops conflicted ops and is only 200 ops wide, so waiting for one
 *  to appear there left ⌘Z dead for the rest of the session after a single
 *  refusal. */
const inFlight = new Map<string, Set<string>>();

function retire(acks: Ack[]): void {
    for (const a of acks) {
        for (const [b, cids] of inFlight) {
            if (cids.delete(a.cid) && !cids.size) inFlight.delete(b);
        }
    }
}
let projection: Projection | null = null;
let status: Record<string, unknown> = {};
let history = false;
let selected: string | null = null;
let language = 'en';
let notice = '';
let noticeTimer: number | undefined;

const LANGUAGES: Array<[string, string]> = [['en', 'English'], ['zh-Hans', '中文（简体）'], ['zh-Hant', '中文（繁體）'], ['ja', '日本語'], ['ko', '한국어'],
    ['es', 'Español'], ['fr', 'Français'], ['de', 'Deutsch'], ['pt', 'Português']];

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text = ''): HTMLElementTagNameMap[K] => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    return e;
};

// ── shell ────────────────────────────────────────────────────────────────────
const shell = el('div', 'codoc-shell');
const tree = el('nav', 'codoc-tree');
const main = el('div', 'codoc-main');
const toolbar = el('div', 'codoc-toolbar');
const page = el('div', 'codoc-page');
const statusBar = el('div', 'codoc-status');
main.append(toolbar, page, statusBar);
shell.append(tree, main);
root.append(shell);

function titleOf(proj: Projection, fid: string): string {
    const b = proj.blocks.find(x => x.id === fid);
    const f = b?.fields.text as { text?: string } | undefined;
    return f?.text?.trim() || 'Untitled';
}

/** Each row's rendered state, so a projection that changed one feature does not
 *  rebuild a thousand rows while the person is typing (every settle publishes
 *  one). */
const rows = new Map<string, { key: string; row: HTMLElement }>();

function renderTree(proj: Projection): void {
    const heads = proj.blocks.filter(b => b.kind === 'heading');
    const want = new Set(heads.map(h => h.id));
    for (const [fid, r] of rows) if (!want.has(fid)) { r.row.remove(); rows.delete(fid); }
    let at: Element | null = tree.firstElementChild;
    for (const h of heads) {
        const phase: Phase = proj.phases[h.id] ?? 'synced';
        const title = titleOf(proj, h.id);
        const key = `${h.level}|${phase}|${title}|${selected === h.id}`;
        let hit = rows.get(h.id);
        if (!hit || hit.key !== key) {
            const row = el('div', `codoc-row codoc-l${h.level} codoc-ch-${PHASE_CHANNEL[phase]}${selected === h.id ? ' is-selected' : ''}`);
            row.dataset.fid = h.id;
            const dot = el('span', `codoc-dot codoc-dot-${phase}`);
            dot.title = PHASE_LABEL[phase];
            row.append(dot, el('span', 'codoc-row-title', title));
            row.addEventListener('click', () => reveal(h.id));
            // Step the cursor off the row first: `at` walks the LIVE children,
            // so when it points at the row being replaced (the ordinary case —
            // rows are walked in order) removing it leaves `insertBefore` a
            // reference node that is no longer a child, which throws.
            if (at === hit?.row) at = at.nextElementSibling;
            hit?.row.remove();
            hit = { key, row };
            rows.set(h.id, hit);
        }
        if (hit.row !== at) tree.insertBefore(hit.row, at);
        else at = at.nextElementSibling;
    }
}

function renderToolbar(proj: Projection): void {
    toolbar.replaceChildren();
    const openPlans = proj.plans.filter(p => p.status === 'open');
    const edited = Object.values(proj.phases).filter(p => p === 'edited').length;
    const queued = Object.values(proj.phases).filter(p => p === 'queued' || p === 'planned').length;
    const title = el('span', 'codoc-crumb', 'tree.codoc');
    const chip = el('span', `codoc-chip codoc-chip-${String(status.state ?? 'synced')}`, String(status.detail || status.state || 'synced'));
    toolbar.append(title, chip, el('span', 'codoc-spacer'));
    const hist = el('button', `codoc-btn${history ? ' is-on' : ''}`, 'History');
    hist.title = 'who wrote which words, and when';
    hist.addEventListener('click', () => {
        history = !history;
        page.classList.toggle('is-history', history);
        editor?.setHistory(history);
        timeline?.toggle(history);
        if (history) send({ kind: 'history' });
        renderToolbar(projection!);
    });
    toolbar.append(hist);
    const lang = el('select', 'codoc-lang');
    lang.title = 'the language the tree is written in';
    for (const [tag, name] of LANGUAGES.some(([t]) => t === language) ? LANGUAGES : [...LANGUAGES, [language, language] as [string, string]]) {
        const o = el('option', '', name); o.value = tag; o.selected = tag === language; lang.append(o);
    }
    lang.addEventListener('change', () => send({ kind: 'set-language', tag: lang.value }));
    toolbar.append(lang);
    const comment = el('button', 'codoc-btn', 'Comment');
    comment.title = 'comment on the selected words (a request the agent can act on)';
    comment.addEventListener('click', () => {
        const a = editor?.selectionAnchor();
        if (!a) { notify('select the words to comment on first'); return; }
        margin?.compose(a.bid, a.text, a.start, a.end);
    });
    toolbar.append(comment);
    if (openPlans.length && decides()) {
        const acceptAll = el('button', 'codoc-btn codoc-btn-plan', `Accept all (${openPlans.length})`);
        acceptAll.addEventListener('click', () => verdicts(openPlans.map(p => p.id), true));
        const rejectAll = el('button', 'codoc-btn', `Reject all (${openPlans.length})`);
        rejectAll.addEventListener('click', () => verdicts(openPlans.map(p => p.id), false));
        toolbar.append(acceptAll, rejectAll);
    }
    if (edited && decides()) {
        const hand = el('button', 'codoc-btn codoc-btn-human', `Hand off (${edited})`);
        hand.title = 'send your edits to the agent (⌘S)';
        hand.addEventListener('click', handoffAll);
        toolbar.append(hand);
    }
    if (queued && decides()) {
        const run = el('button', 'codoc-btn', 'Build now');
        run.title = 'run codoc realize in a terminal';
        run.addEventListener('click', () => send({ kind: 'realize' }));
        toolbar.append(run);
    }
}

/** Whether this viewer may answer a proposal or ask for a build. A contributor
 *  signed in with `suggest` writes plans of their own (02 §3.6) and the daemon
 *  wraps their typing in one; a verdict or a hand-off from them is refused, so
 *  the page must not offer it (the button would be a lie, and its refusal would
 *  reload the paragraph under their hands). */
function decides(): boolean {
    return (typeof status.role === 'string' ? status.role : 'owner') !== 'suggest';
}

function renderStatus(): void {
    const n = projection ? projection.blocks.filter(b => b.kind === 'heading').length : 0;
    statusBar.textContent = notice || `${n} features · ${String(status.state ?? 'synced')}`;
    statusBar.classList.toggle('is-notice', !!notice);
}

function paint(what: string, draw: () => void): void {
    try {
        draw();
    } catch (e) {
        console.error(`codoc: ${what} could not be drawn`, e);
        notify(`${what} could not be drawn; the page may be behind`);
    }
}

function notify(text: string): void {
    notice = text;
    renderStatus();
    if (noticeTimer) window.clearTimeout(noticeTimer);
    noticeTimer = window.setTimeout(() => { notice = ''; renderStatus(); }, 6000);
}

// ── actions ──────────────────────────────────────────────────────────────────
let seq = 0;
// A per-page prefix, for the same reason as in the editor: two windows of one
// client must not mint the same id in the same millisecond.
const tab = Math.random().toString(36).slice(2, 8);
const cid = () => `ide-${tab}-${Date.now().toString(36)}-${(seq++).toString(36)}`;
const batch = () => `t-${Math.random().toString(16).slice(2, 14)}`;

function submit(subs: Submission[]): void {
    for (const s of subs) {
        const cids = inFlight.get(s.batch) ?? new Set<string>();
        cids.add(s.cid);
        inFlight.set(s.batch, cids);
    }
    send({ kind: 'submit', subs });
}

function verdicts(pids: string[], accept: boolean): void {
    editor?.flush();
    const b = batch();
    submit(pids.map(pid => ({ cid: cid(), batch: b, kind: accept ? 'plan.accept' : 'plan.reject', target: { pid }, payload: {} })));
}

function buildThread(t: Thread): void {
    if (!projection) return;
    editor?.flush();
    const fid = featureOfBlock(t.anchor);
    if (!fid) return;
    if (!decides()) { notify('your note is saved; a maintainer hands the feature off'); return; }
    const asked = t.messages.map(m => m.body).join(' ');
    submit([{ cid: cid(), batch: batch(), kind: 'feature.handoff', target: { fid }, payload: { asked } }]);
    send({ kind: 'realize' });
}

function featureOfBlock(bid: string): string | null {
    if (!projection) return null;
    let fid: string | null = null;
    for (const b of projection.blocks) {
        if (b.kind === 'heading') fid = b.id;
        if (b.id === bid) return fid;
    }
    return null;
}

function handoffAll(): void {
    if (!projection) return;
    editor?.flush();
    const b = batch();
    // No `covers`: the applier fills it with its own frontier, which includes
    // the ops we just flushed. The projection's frontier predates them.
    const fids = Object.entries(projection.phases).filter(([, p]) => p === 'edited').map(([f]) => f);
    submit(fids.map(fid => ({ cid: cid(), batch: b, kind: 'feature.handoff', target: { fid }, payload: {} })));
}

function reveal(fid: string): void {
    selected = fid;
    if (!editor) return;
    const dom = editor.view.dom.querySelector(`[data-bid="${fid}"]`) as HTMLElement | null;
    dom?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    tree.querySelectorAll('.codoc-row').forEach(r => r.classList.toggle('is-selected', (r as HTMLElement).dataset.fid === fid));
}

function undo(redo: boolean): void {
    editor?.flush();
    pendingUndo = redo ? 'redo' : 'undo';
    send({ kind: 'history' });
}

function applyUndo(w: HistoryWindow): void {
    if (!pendingUndo) return;
    for (const e of w.ops) inFlight.delete(e.batch);
    if (inFlight.size) return;                    // something we sent is still unanswered; the ack will ask again
    const redo = pendingUndo === 'redo';
    const next = nextUndo(w, actor, redo);
    pendingUndo = null;
    if (!next) { notify(redo ? 'nothing to redo' : 'nothing to undo'); return; }
    submit(next.ops.map(op => ({ cid: cid(), batch: next.batch, ...op })));
}

document.addEventListener('keydown', ev => {
    const mac = navigator.platform.toUpperCase().includes('MAC');
    const mod = mac ? ev.metaKey : ev.ctrlKey;
    if (mod && ev.key.toLowerCase() === 's') { ev.preventDefault(); handoffAll(); }
    else if (mod && ev.key.toLowerCase() === 'z') { ev.preventDefault(); undo(ev.shiftKey); }
    else if (mod && ev.key.toLowerCase() === 'y') { ev.preventDefault(); undo(true); }
    else if (mod && ev.key.toLowerCase() === 'f') { ev.preventDefault(); editor?.find(ev.altKey); }
    else if (mod && ev.key.toLowerCase() === 'h') { ev.preventDefault(); editor?.find(true); }
});

// ── messages ─────────────────────────────────────────────────────────────────
window.addEventListener('message', (ev: MessageEvent<HostToWebview>) => {
    // On the hub this is an ordinary browser tab, and an opener can post into
    // it: a crafted `history` with `pendingUndo` set would make the page submit
    // the sender's ops under the viewer's own session. Our own messages come
    // from this window (the hub relays them with `window.postMessage`) or, in a
    // VS Code webview, from the frame that hosts it.
    if (ev.origin && ev.origin !== window.location.origin && ev.source !== window.parent) return;
    const m = ev.data;
    switch (m.kind) {
        case 'projection': {
            if (!m.proj) return;
            projection = m.proj;
            const realizing = new Set(m.realizing ?? []);
            if (!editor) {
                editor = mountEditor(page, m.proj, {
                    submit,
                    onFeature: fid => { if (fid && fid !== selected) { selected = fid; tree.querySelectorAll('.codoc-row').forEach(r => r.classList.toggle('is-selected', (r as HTMLElement).dataset.fid === fid)); } },
                    openCite: (file, symbol) => send({ kind: 'open-cite', file, symbol }),
                    onConflict: (_bid, conflict) => notify(`an edit could not apply (${conflict}); the paragraph was reloaded`),
                });
                timeline = new Timeline(page, () => editor?.view.dom ?? null);
                margin = new Margin(page, () => editor?.view ?? null, {
                    submit: ops => { const b = batch(); submit(ops.map(op => ({ cid: cid(), batch: b, ...op }))); },
                    build: buildThread,
                    newBid: () => `b-${Math.random().toString(16).slice(2, 14).padEnd(12, '0')}`,
                    featureOf: featureOfBlock,
                });
            } else {
                editor.adopt(m.proj, realizing);
            }
            // Each pane is an independent view of ONE projection, so a throw in
            // one must not cost the others their update: a single bad tree row
            // used to skip the toolbar, the status, the margin and the undo
            // re-request as well, and leave the person a page that had quietly
            // stopped following the store.
            paint('the tree pane', () => renderTree(m.proj!));
            paint('the toolbar', () => { renderToolbar(m.proj!); renderStatus(); });
            paint('the margin', () => margin?.update(m.proj!));
            if (history || pendingUndo) send({ kind: 'history' });
            return;
        }
        case 'history':
            if (m.history) {
                if (projection) timeline?.setWindow(m.history as HistoryWindow, projection);
                applyUndo(m.history as HistoryWindow);
            }
            return;
        case 'acks':
            if (m.acks) { retire(m.acks as Ack[]); editor?.ack(m.acks as Ack[]); }
            if (pendingUndo) send({ kind: 'history' });
            return;
        case 'status':
            status = m.status ?? {};
            if (typeof m.status?.doc_language === 'string') language = m.status.doc_language;
            if (typeof m.status?.actor === 'string') actor = m.status.actor;
            if (projection) renderToolbar(projection);
            renderStatus();
            return;
        case 'notice':
            if (m.text) notify(m.text);
            return;
        case 'find':
            editor?.find(!!m.replace);
            return;
        case 'reveal':
            if (m.fid) reveal(m.fid);
            return;
    }
});

send({ kind: 'ready' });
