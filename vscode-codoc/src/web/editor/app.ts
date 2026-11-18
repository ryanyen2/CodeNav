/**
 * app.ts: the editor page, made of a tree pane, a toolbar, the document, a margin of
 * comment cards and a status line.
 *
 * This is the entry of the page bundle, and it runs as soon as it loads. It
 * builds the shell, tells the host it is ready, and from then on is driven by
 * the host's messages (wire.ts). The first projection mounts the editor. Each
 * later one is adopted by the editor and redraws the tree rows, the toolbar
 * and the margin. Toolbar buttons and the page's own keys (hand off, undo,
 * find) submit ops or ask the host for something.
 *
 * The page is the same in a VS Code webview and in a browser tab on the hub.
 * `acquireVsCodeApi` is VS Code's in the first and web/hub.ts's in the second.
 */
import './app.css';

import type { HostToPage, PageToHost } from '../../wire';
import { PHASE_CHANNEL, PHASE_LABEL } from '../calc/claims';
import { newBatch, newBid, newCid } from '../calc/ids';
import type { Ack, OpBody, Phase, Projection, Submission } from '../calc/projection';
import { nextUndo, type HistoryWindow } from '../calc/window';
import { Margin, type Thread } from './comments';
import { mountEditor, type EditorHandle } from './editor';
import { Timeline } from './timeline';

declare function acquireVsCodeApi(): { postMessage(m: unknown): void };

const api = typeof acquireVsCodeApi === 'function' ? acquireVsCodeApi() : { postMessage: () => undefined };

const send = (m: PageToHost) => api.postMessage(m);

const root = document.getElementById('app')!;
let editor: EditorHandle | null = null;
let margin: Margin | null = null;
let timeline: Timeline | null = null;
/** Who the daemon says this viewer is. Undo inverts this actor's batches only. */
let actor = 'human:local';
let pendingUndo: 'undo' | 'redo' | null = null;
/** Batch → the client ids it submitted that the daemon has not answered. Undo
 *  waits for these, because it must choose from a window that already carries
 *  the person's newest work. A batch leaves this map by its acks and not by
 *  appearing in the window: the window omits refused ops and holds only the
 *  newest 200, so a batch can be answered and never show up there. */
const inFlight = new Map<string, Set<string>>();

/** An undo sent and not yet answered: its batch, what it undoes, the newest history
 *  position when it was sent, and the blocks the undone batch touched. */
type Undoing = { batch: string; what: string; pos: number; blocks: Set<string> };
const undoing = new Map<string, Undoing>();
/** Undo batches the daemon refused. The next undo moves past them. One is offered again
 *  only when it may now succeed: its refusal was not final, and someone else has since
 *  changed the document's structure or a block it touches (typing elsewhere does not count). */
const refusedUndo = new Map<string, Undoing & { conflict: string }>();
/** Refusals no later change answers: the undo can never apply. */
const FINAL = new Set(['authority', 'verdict_folded', 'not_current_verdict', 'plan_not_open']);
/** Ops that change which blocks exist or where: any of them may make a refused undo possible. */
const STRUCTURE = new Set(['block.add', 'block.delete', 'block.restore', 'block.move', 'block.split', 'block.merge', 'block.set']);
/** The blocks an entry changed: its text snapshots and the blocks its inverse names. */
const touchedBy = (e: HistoryWindow['ops'][number]): string[] => [
    ...Object.keys(e.blocks),
    ...e.inverse.flatMap(o => [o.target.bid, o.target.into, ...((o.payload as { ids?: unknown }).ids as string[] ?? [])]),
].filter((x): x is string => typeof x === 'string');
/** Why an undo was refused, in the person's words; any other code is shown as it is. */
const WHY_NOT: Record<string, string> = {
    verdict_folded: 'it is in a snapshot now',
    not_current_verdict: 'the proposal was decided again since',
    plan_open: 'the proposal is open again',
    plan_not_open: 'the proposal was decided since',
    authority: 'your role does not allow it',
};

function retire(acks: Ack[]): void {
    for (const a of acks) {
        for (const [b, cids] of inFlight) {
            if (cids.delete(a.cid) && !cids.size) inFlight.delete(b);
        }
        const u = undoing.get(a.cid);
        if (!u) continue;
        undoing.delete(a.cid);
        if (a.conflict && !refusedUndo.has(u.batch)) {
            refusedUndo.set(u.batch, { ...u, conflict: a.conflict });
            notify(`Could not ${u.what}: ${WHY_NOT[a.conflict] ?? a.conflict}.`);
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
        const key = `${h.level}|${phase}|${title}|${selected === h.id}`;
        let hit = rows.get(h.id);
        if (!hit || hit.key !== key) {
            const row = el('div', `codoc-row codoc-l${h.level} codoc-ch-${PHASE_CHANNEL[phase]}${selected === h.id ? ' is-selected' : ''}`);
            row.dataset.fid = h.id;
            const dot = el('span', `codoc-dot codoc-dot-${phase}`);
            dot.title = PHASE_LABEL[phase];
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

/** Whether this viewer may answer a plan or ask for a build. A hub contributor
 *  with the `suggest` role cannot: the daemon wraps their typing in a plan of
 *  their own and refuses a verdict or a hand-off from them, so the page does
 *  not offer either. */
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
function submit(subs: Submission[]): void {
    for (const s of subs) {
        const cids = inFlight.get(s.batch) ?? new Set<string>();
        cids.add(s.cid);
        inFlight.set(s.batch, cids);
    }
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
 *  frontier, which includes the ops flushed before it. */
const handoff = (fid: string, asked?: string): OpBody =>
    ({ kind: 'feature.handoff', target: { fid }, payload: asked ? { asked } : {} });

/** Accept or reject plans, in one batch. Accepting also hands off each feature
 *  the plan lists in `handoff`, so what was accepted gets built. */
function verdicts(pids: string[], accept: boolean): void {
    const ops: OpBody[] = pids.map(pid => ({ kind: accept ? 'plan.accept' : 'plan.reject', target: { pid }, payload: {} }));
    if (accept) {
        const asked = new Map<string, string[]>();
        for (const p of projection?.plans ?? []) {
            if (pids.includes(p.id)) for (const fid of p.handoff ?? []) asked.set(fid, [...(asked.get(fid) ?? []), `Build the plan ${p.id}.`]);
        }
        for (const [fid, asks] of asked) ops.push(handoff(fid, asks.join(' ')));
    }
    submitOps(ops);
}

/** Build it: hand the thread's feature off with the comment as the request, then run the build. */
function buildThread(t: Thread): void {
    const fid = projection && featureOfBlock(t.anchor);
    if (!fid) return;
    if (!decides()) { notify('your note is saved; a maintainer hands the feature off'); return; }
    submitOps([handoff(fid, t.messages.map(m => m.body).join(' '))]);
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
    submitOps(Object.entries(projection.phases).filter(([, p]) => p === 'edited').map(([fid]) => handoff(fid)));
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

/** Undo or redo: ask for the history window, and invert a batch when it arrives (`applyUndo`). */
function undo(redo: boolean): void {
    editor?.flush();
    pendingUndo = redo ? 'redo' : 'undo';
    send({ kind: 'history' });
}

function applyUndo(w: HistoryWindow): void {
    if (!pendingUndo) return;
    for (const e of w.ops) inFlight.delete(e.batch);
    if (inFlight.size) return;                    // something sent is still unanswered; its ack asks again
    const redo = pendingUndo === 'redo';
    for (const [b, r] of refusedUndo) {
        const changed = w.ops.some(e => e.actor !== actor && e.pos > r.pos
            && (STRUCTURE.has(e.kind) || touchedBy(e).some(x => r.blocks.has(x))));
        if (!FINAL.has(r.conflict) && changed) refusedUndo.delete(b);
    }
    const next = nextUndo(w, actor, redo, new Set(refusedUndo.keys()));
    pendingUndo = null;
    if (!next) { notify(redo ? 'nothing to redo' : 'nothing to undo'); return; }
    const subs = next.ops.map(op => ({ cid: newCid(), batch: next.batch, ...op }));
    // What the person did in the original batch, by its op kinds, for the notice if the daemon refuses.
    const original = next.batch.replace(/^(u-)+/, '');
    const entries = w.ops.filter(e => e.batch === original || e.batch === next.batch.slice(2));
    const kinds = new Set(entries.filter(e => e.batch === original).map(e => e.kind));
    const blocks = new Set(entries.flatMap(touchedBy));
    const did = kinds.has('plan.accept') ? 'accepting the proposal' : kinds.has('plan.reject') ? 'rejecting the proposal'
        : kinds.has('feature.handoff') ? 'the hand-off' : 'the edit';
    const what = `${redo ? 'redo' : 'undo'} ${did}`;
    const pos = Math.max(0, ...w.ops.map(e => e.pos));
    for (const s of subs) undoing.set(s.cid, { batch: next.batch, what, pos, blocks });
    submit(subs);
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
            const realizing = new Set(m.realizing ?? []);
            if (!editor) {
                editor = mountEditor(page, proj, {
                    submit,
                    onFeature: fid => { if (fid && fid !== selected) select(fid); },
                    openCite: (file, symbol) => send({ kind: 'open-cite', file, symbol }),
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
                paint('the document', () => editor!.adopt(proj, realizing));
            }
            // Each pane is its own view of the one projection and is drawn
            // under `paint`, so a throw in one does not stop the others from
            // following the store.
            paint('the tree pane', () => renderTree(proj));
            paint('the toolbar', () => { renderToolbar(proj); renderStatus(); });
            paint('the margin', () => margin?.update(proj));
            if (history || pendingUndo) send({ kind: 'history' });
            return;
        }
        case 'history':
            if (m.history) {
                if (projection) timeline?.setWindow(m.history, projection);
                applyUndo(m.history);
            }
            return;
        case 'acks':
            if (m.acks) { retire(m.acks); editor?.ack(m.acks); }
            if (pendingUndo) send({ kind: 'history' });
            return;
        case 'status':
            status = m.status ?? {};
            if (typeof status.doc_language === 'string') language = status.doc_language;
            if (typeof status.actor === 'string') actor = status.actor;
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
