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

function renderTree(proj: Projection): void {
    tree.replaceChildren();
    const heads = proj.blocks.filter(b => b.kind === 'heading');
    for (const h of heads) {
        const phase: Phase = proj.phases[h.id] ?? 'synced';
        const row = el('div', `codoc-row codoc-l${h.level} codoc-ch-${PHASE_CHANNEL[phase]}${selected === h.id ? ' is-selected' : ''}`);
        row.dataset.fid = h.id;
        const dot = el('span', `codoc-dot codoc-dot-${phase}`);
        dot.title = PHASE_LABEL[phase];
        row.append(dot, el('span', 'codoc-row-title', titleOf(proj, h.id)));
        row.addEventListener('click', () => reveal(h.id));
        tree.append(row);
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
    if (openPlans.length) {
        const acceptAll = el('button', 'codoc-btn codoc-btn-plan', `Accept all (${openPlans.length})`);
        acceptAll.addEventListener('click', () => verdicts(openPlans.map(p => p.id), true));
        const rejectAll = el('button', 'codoc-btn', `Reject all (${openPlans.length})`);
        rejectAll.addEventListener('click', () => verdicts(openPlans.map(p => p.id), false));
        toolbar.append(acceptAll, rejectAll);
    }
    if (edited) {
        const hand = el('button', 'codoc-btn codoc-btn-human', `Hand off (${edited})`);
        hand.title = 'send your edits to the agent (⌘S)';
        hand.addEventListener('click', handoffAll);
        toolbar.append(hand);
    }
    if (queued) {
        const run = el('button', 'codoc-btn', 'Build now');
        run.title = 'run codoc realize in a terminal';
        run.addEventListener('click', () => send({ kind: 'realize' }));
        toolbar.append(run);
    }
}

function renderStatus(): void {
    const n = projection ? projection.blocks.filter(b => b.kind === 'heading').length : 0;
    statusBar.textContent = notice || `${n} features · ${String(status.state ?? 'synced')}`;
    statusBar.classList.toggle('is-notice', !!notice);
}

function notify(text: string): void {
    notice = text;
    renderStatus();
    if (noticeTimer) window.clearTimeout(noticeTimer);
    noticeTimer = window.setTimeout(() => { notice = ''; renderStatus(); }, 6000);
}

// ── actions ──────────────────────────────────────────────────────────────────
let seq = 0;
const cid = () => `ide-${Date.now().toString(36)}-${(seq++).toString(36)}`;
const batch = () => `t-${Math.random().toString(16).slice(2, 14)}`;

function submit(subs: Submission[]): void {
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
    const asked = t.messages.map(m => m.body).join(' ');
    submit([{ cid: cid(), batch: batch(), kind: 'feature.handoff', target: { fid }, payload: { covers: projection.frontier, asked } }]);
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
    const fids = Object.entries(projection.phases).filter(([, p]) => p === 'edited').map(([f]) => f);
    submit(fids.map(fid => ({ cid: cid(), batch: b, kind: 'feature.handoff', target: { fid }, payload: { covers: projection!.frontier } })));
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
    const next = nextUndo(w, actor, pendingUndo === 'redo');
    pendingUndo = null;
    if (!next) { notify('nothing to undo'); return; }
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
            renderTree(m.proj);
            renderToolbar(m.proj);
            renderStatus();
            margin?.update(m.proj);
            if (history) send({ kind: 'history' });
            return;
        }
        case 'history':
            if (m.history) {
                if (projection) timeline?.setWindow(m.history as HistoryWindow, projection);
                applyUndo(m.history as HistoryWindow);
            }
            return;
        case 'acks':
            if (m.acks) editor?.ack(m.acks as Ack[]);
            return;
        case 'status':
            status = m.status ?? {};
            if (typeof m.status?.doc_language === 'string') language = m.status.doc_language;
            if (typeof m.status?.actor === 'string') actor = m.status.actor;
            if (projection) renderToolbar(projection);
            renderStatus();
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
