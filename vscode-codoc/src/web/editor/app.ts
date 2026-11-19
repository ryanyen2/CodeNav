/**
 * app.ts: the editor page, made of a tree pane, a toolbar, the document, a margin of
 * comment cards and a status line.
 *
 * This is the entry of the page bundle, and it runs as soon as it loads. It
 * builds the shell, tells the host it is ready, and from then on is driven by
 * the host's messages (wire.ts). The first projection mounts the editor. Each
 * later one is adopted by the editor and redraws the tree rows, the toolbar
 * and the margin. Toolbar buttons and the page's own keys (hand off, undo,
 * find) submit ops or ask the host for something; what undo needs to know is
 * kept in undo.ts.
 *
 * The page is the same in a VS Code webview and in a browser tab on the hub.
 * `acquireVsCodeApi` is VS Code's in the first and web/hub.ts's in the second.
 */
import './app.css';

import type { HostToPage, PageToHost } from '../../wire';
import { PHASE_CHANNEL, PHASE_LABEL, theirEdits } from '../calc/claims';
import { newBatch, newBid, newCid } from '../calc/ids';
import type { OpBody, Phase, Projection, Submission } from '../calc/projection';
import { Margin, type Thread } from './comments';
import { mountEditor, type EditorHandle } from './editor';
import { Timeline } from './timeline';
import { Undo } from './undo';

declare function acquireVsCodeApi(): { postMessage(m: unknown): void; getState?(): unknown; setState?(s: unknown): void };

const api = typeof acquireVsCodeApi === 'function' ? acquireVsCodeApi() : { postMessage: () => undefined };

const send = (m: PageToHost) => api.postMessage(m);

const root = document.getElementById('app')!;
let editor: EditorHandle | null = null;
let margin: Margin | null = null;
let timeline: Timeline | null = null;
/** Who the daemon says this viewer is (`status.actor`); only their words are inked. */
let actor = '';
const undo = new Undo(api);
let projection: Projection | null = null;
let status: Record<string, unknown> = {};
let history = false;
let selected: string | null = null;
let language = 'en';
let notice = '';
let noticeTimer: number | undefined;
const MAC = navigator.platform.toUpperCase().includes('MAC');
/** A shortcut as this platform writes it: ⌘S on a Mac, Ctrl+S elsewhere. */
const shortcut = (key: string): string => (MAC ? `⌘${key}` : `Ctrl+${key}`);

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

/** Each row as last drawn. A projection arrives with every settle, and one
 *  that changed a single feature redraws that row and leaves the rest. */
const rows = new Map<string, { key: string; row: HTMLElement }>();

function renderTree(proj: Projection): void {
    const heads = proj.blocks.filter(b => b.kind === 'heading');
    const want = new Set(heads.map(h => h.id));
    for (const [fid, r] of rows) if (!want.has(fid)) { r.row.remove(); rows.delete(fid); }
    let at: Element | null = tree.firstElementChild;
    for (const h of heads) {
        const phase: Phase = proj.phases[h.id] ?? 'synced';
        const title = titleOf(proj, h.id);
        const theirs = theirEdits(proj, h.id, actor);
        const key = `${h.level}|${phase}|${title}|${selected === h.id}|${theirs}`;
        let hit = rows.get(h.id);
        if (!hit || hit.key !== key) {
            const row = el('div', `codoc-row codoc-l${h.level} codoc-ch-${PHASE_CHANNEL[phase]}${selected === h.id ? ' is-selected' : ''}`);
            row.dataset.fid = h.id;
            const dot = el('span', `codoc-dot codoc-dot-${phase}${theirs ? ' is-theirs' : ''}`);
            dot.title = theirs || PHASE_LABEL[phase];
            row.append(dot, el('span', 'codoc-row-title', title));
            row.addEventListener('click', () => reveal(h.id));
            // Step the cursor off the row before removing it. `at` walks the
            // live children and usually points at the row being replaced;
            // `insertBefore` throws on a reference node that has been removed.
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
    const edited = mine(proj).length;
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
        hand.title = `send your edits to the agent (${shortcut('S')})`;
        hand.addEventListener('click', handoffAll);
        toolbar.append(hand);
    }
    if (queued && decides() && terminal()) {
        const run = el('button', 'codoc-btn', 'Build now');
        run.title = 'run codoc realize in a terminal';
        run.addEventListener('click', () => send({ kind: 'realize' }));
        toolbar.append(run);
    }
}

/** Whether this viewer may answer a plan or ask for a build. A hub contributor
 *  with the `suggest` role cannot: the daemon wraps their typing in a plan of
 *  their own and refuses a verdict or a hand-off from them, so the page does
 *  not offer either. */
function decides(): boolean {
    return (typeof status.role === 'string' ? status.role : 'owner') !== 'suggest';
}

/** Whether the host can run `codoc realize` and open code: VS Code can, the hub
 *  cannot. A host that does not say is taken to be VS Code. */
function terminal(): boolean {
    return status.terminal !== false;
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
function submit(subs: Submission[]): void {
    undo.track(subs);
    send({ kind: 'submit', subs });
}

/** Send `ops` as one new batch, after the editor's queued ops: an anchor may
 *  name `$cid` refs of ops still waiting out the settle, and the daemon
 *  resolves those only in submission order. */
function submitOps(ops: OpBody[]): void {
    editor?.flush();
    const batch = newBatch();
    submit(ops.map(op => ({ cid: newCid(), batch, ...op })));
}

/** A hand-off of `fid`. No `covers`: the applier fills it with its own
 *  frontier, which includes the ops flushed before it. No `asked` either: that
 *  is for words the person types to go with a hand-off, and the page has none. */
const handoff = (fid: string): OpBody => ({ kind: 'feature.handoff', target: { fid }, payload: {} });

/** Accept or reject plans, in one batch. Accepting also hands off each feature
 *  the plan lists in `hands_off`, so what was accepted gets built. */
function verdicts(pids: string[], accept: boolean): void {
    const ops: OpBody[] = pids.map(pid => ({ kind: accept ? 'plan.accept' : 'plan.reject', target: { pid }, payload: {} }));
    if (accept) {
        const fids = new Set<string>();
        for (const p of projection?.plans ?? []) if (pids.includes(p.id)) for (const fid of p.hands_off ?? []) fids.add(fid);
        for (const fid of fids) ops.push(handoff(fid));
    }
    submitOps(ops);
}

/** Build it: hand the thread's feature off, and run the build where there is a
 *  terminal. The thread itself is the request: an open comment reaches the
 *  agent's directive through the comment kind. */
function buildThread(t: Thread): void {
    const fid = featureOfBlock(t.anchor);
    if (!fid) return;
    submitOps([handoff(fid)]);
    if (terminal()) send({ kind: 'realize' });
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

/** The `edited` features with this viewer's own edits: what ⌘S hands off. A
 *  teammate's unsent edits go out with their own ⌘S. */
function mine(proj: Projection): string[] {
    return Object.keys(proj.phases).filter(fid => proj.phases[fid] === 'edited' && !theirEdits(proj, fid, actor));
}

function handoffAll(): void {
    if (!projection) return;
    submitOps(mine(projection).map(fid => handoff(fid)));
}

function select(fid: string): void {
    selected = fid;
    tree.querySelectorAll('.codoc-row').forEach(r => r.classList.toggle('is-selected', (r as HTMLElement).dataset.fid === fid));
}

/** A tree row was clicked: scroll its heading into view. */
function reveal(fid: string): void {
    select(fid);
    const dom = editor?.view.dom.querySelector(`[data-bid="${fid}"]`) as HTMLElement | null | undefined;
    dom?.scrollIntoView({ block: 'center', behavior: 'smooth' });
}

/** Undo or redo: ask for the history window; `Undo.onHistory` answers when it arrives. */
function askUndo(redo: boolean): void {
    editor?.flush();
    undo.ask(redo);
    send({ kind: 'history' });
}

document.addEventListener('keydown', ev => {
    const mod = MAC ? ev.metaKey : ev.ctrlKey;
    if (mod && ev.key.toLowerCase() === 's') { ev.preventDefault(); handoffAll(); }
    else if (mod && ev.key.toLowerCase() === 'z') { ev.preventDefault(); askUndo(ev.shiftKey); }
    else if (mod && ev.key.toLowerCase() === 'y') { ev.preventDefault(); askUndo(true); }
    else if (mod && ev.key.toLowerCase() === 'f') { ev.preventDefault(); editor?.find(ev.altKey); }
    else if (mod && ev.key.toLowerCase() === 'h') { ev.preventDefault(); editor?.find(true); }
});

// ── messages ─────────────────────────────────────────────────────────────────
window.addEventListener('message', (ev: MessageEvent<HostToPage>) => {
    // On the hub this is an ordinary browser tab, and an opener can post into
    // it: a crafted `history` while an undo is pending would make the page
    // submit the sender's ops under the viewer's own session. The page's own
    // messages come from this window (the hub shim relays them with
    // `window.postMessage`) or, in a VS Code webview, from the frame that hosts it.
    if (ev.origin && ev.origin !== window.location.origin && ev.source !== window.parent) return;
    const m = ev.data;
    switch (m.kind) {
        case 'projection': {
            const proj = m.proj;
            if (!proj) return;
            projection = proj;
            if (!editor) {
                editor = mountEditor(page, proj, {
                    submit,
                    onFeature: fid => { if (fid && fid !== selected) select(fid); },
                    openCite: symbol => { if (terminal()) send({ kind: 'open-cite', symbol }); else notify('the code opens in the editor, not on the hub'); },
                    onConflict: (bid, conflict) => notify(bid ? `an edit could not apply (${conflict}); the block was reloaded` : conflict),
                    actor: () => actor,
                });
                timeline = new Timeline(page, () => editor?.view.dom ?? null);
                margin = new Margin(page, () => editor?.view ?? null, {
                    submit: submitOps,
                    build: buildThread,
                    newBid,
                    featureOf: featureOfBlock,
                    me: () => ({ actor, decides: decides() }),
                });
            } else {
                paint('the document', () => editor!.adopt(proj));
            }
            // Each pane is its own view of the one projection and is drawn
            // under `paint`, so a throw in one does not stop the others from
            // following the store.
            paint('the tree pane', () => renderTree(proj));
            paint('the toolbar', () => { renderToolbar(proj); renderStatus(); });
            paint('the margin', () => margin?.update(proj));
            if (history || undo.waiting) send({ kind: 'history' });
            return;
        }
        case 'history':
            if (m.history) {
                if (projection) timeline?.setWindow(m.history, projection);
                const r = undo.onHistory(m.history);
                if (typeof r === 'string') notify(r);
                else if (r) submit(r);
            }
            return;
        case 'acks':
            if (m.acks) {
                const refused = undo.onAcks(m.acks);
                if (refused) notify(refused);
                editor?.ack(m.acks);
            }
            if (undo.waiting) send({ kind: 'history' });
            return;
        case 'status':
            status = m.status ?? {};
            if (typeof status.doc_language === 'string') {
                language = status.doc_language;
                document.documentElement.lang = language;
            }
            if (typeof status.actor === 'string' && status.actor !== actor) {
                actor = status.actor;
                editor?.repaint();
                if (projection) renderTree(projection);
            }
            if (projection) renderToolbar(projection);
            renderStatus();
            return;
        case 'notice':
            if (m.text) notify(m.text);
            return;
        case 'find':
            editor?.find(!!m.replace);
            return;
    }
});

send({ kind: 'ready' });
