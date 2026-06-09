/**
 * doc-view.ts — the bundled webview client for the Codoc Tree custom editor.
 *
 * Left: the feature tree (navigation). Right: ONE whole-doc rich-text editor over
 * the entire tree (headings = features) — see ./tiptap/whole-doc-editor. Selecting
 * a tree row scrolls the editor to that feature; the editor's caret / scroll-spy
 * highlights the tree row back. The `.codoc` text file stays the source of truth;
 * edits flow to the host as `doc-settle` / `suggest-*` / `verdict` / `move` messages.
 */

import './doc-view.css';
import { mountWholeDocEditor, WholeDocEditorHandle } from './tiptap/whole-doc-editor';
import { AuthorController } from './tiptap/author-plugin';
import { kindGlyph } from '../state/grammar';
import type { DocPayload, UINode, WebviewMessage } from './protocol';

declare function acquireVsCodeApi(): { postMessage(msg: WebviewMessage): void };
const vscode = acquireVsCodeApi();

const EMPTY: DocPayload = {
    nodes: {}, roots: [],
    status: { state: 'in_sync', pending: 0 },
    sync: { state: 'in_sync', pending: 0, activeWrite: [], activeRead: [], phase: {} },
    rootName: '', pendingEventIds: [], rev: 0,
};

let payload: DocPayload = EMPTY;
// The active authoring instrument (pen/pencil + role). Persists across edits so
// the user's chosen mode sticks.
const authorController = new AuthorController();
// The whole-doc editor — one TipTap instance over the entire tree.
let wholeEditor: WholeDocEditorHandle | null = null;
// Guard: while the editor's own selection drives the tree highlight, don't scroll
// the editor back (would fight the user's caret).
let syncingFromEditor = false;
const expanded = new Set<string>();
let selectedId: string | null = null;
let firstPayload = true;
let dragSourceId: string | null = null;
let lastRev = -1;
let didFocusTree = false;
let mounted = false; // first payload builds the shell; the rest reconcile

function focusTree(): void {
    (document.querySelector('.tree') as HTMLElement | null)?.focus({ preventScroll: true });
}

const app = document.getElementById('app')!;

// ─── DOM helpers ────────────────────────────────────────────────────────────
function el<K extends keyof HTMLElementTagNameMap>(
    tag: K, cls?: string | null, text?: string,
): HTMLElementTagNameMap[K] {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
}

function leafSym(s: string): string {
    const i = s.indexOf('::');
    const tail = i >= 0 ? s.slice(i + 2) : s;
    return tail === '__module__' ? '‹module›' : tail.split('::').pop() ?? tail;
}

function cssEsc(s: string): string {
    return (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/["\\]/g, '\\$&');
}

function statusLabel(s: string, n: number): string {
    if (s === 'in_sync') return 'in sync';
    if (s === 'code_drift') return n + ' proposal' + (n === 1 ? '' : 's');
    if (s === 'tree_dirty') return 'applying tree edits…';
    if (s === 'awaiting_impl') return n + ' to implement';
    if (s === 'realizing') {
        const r = payload.sync.realize;
        return r && r.total ? `implementing ${r.done + 1} of ${r.total}…` : 'implementing…';
    }
    return s;
}

function postVerdict(eventIds: string[], accept: boolean): void {
    vscode.postMessage({ kind: 'verdict', eventIds, accept });
}

// ── Optimistic verdict feedback ──────────────────────────────────────────────
// A verdict only writes inbox.json; the authoritative update arrives async when a
// loop drains it and the sidecar refreshes. beginApplying() shows "applying…";
// endApplying() clears it when the next payload lands. A safety timer reverts the
// state if nothing drains the inbox (e.g. no daemon) so controls never stick.
let applyingTimer = 0;
function beginApplying(group: HTMLElement | null): void {
    document.body.classList.add('applying');
    if (group) {
        group.classList.add('applying');
        group.querySelectorAll('button').forEach(b => { (b as HTMLButtonElement).disabled = true; });
    }
    if (applyingTimer) clearTimeout(applyingTimer);
    applyingTimer = window.setTimeout(endApplying, 5000);
}
function endApplying(): void {
    if (applyingTimer) { clearTimeout(applyingTimer); applyingTimer = 0; }
    document.body.classList.remove('applying');
    document.querySelectorAll('.applying').forEach(e => e.classList.remove('applying'));
}

/** After a tree re-render, re-disable verdict controls if a verdict is still
 *  in-flight (body.applying) — else freshly-built buttons become clickable again
 *  mid-apply and a duplicate verdict can fire. */
function reapplyApplyingTo(root: ParentNode): void {
    if (!document.body.classList.contains('applying')) return;
    root.querySelectorAll('.verdict, .inline-verdict').forEach(g => {
        g.classList.add('applying');
        g.querySelectorAll('button').forEach(b => { (b as HTMLButtonElement).disabled = true; });
    });
}

function verdictButtons(eventId: string): HTMLElement {
    const wrap = el('span', 'verdict');
    const acc = el('button', 'v-accept', '✓'); acc.title = 'Accept';
    acc.onclick = ev => { ev.stopPropagation(); beginApplying(wrap); postVerdict([eventId], true); };
    const rej = el('button', 'v-reject', '✗'); rej.title = 'Reject';
    rej.onclick = ev => { ev.stopPropagation(); beginApplying(wrap); postVerdict([eventId], false); };
    wrap.append(rej, acc);
    return wrap;
}

function isDescendant(ancestorId: string, candidateId: string): boolean {
    let cur: UINode | undefined = payload.nodes[candidateId];
    while (cur && cur.parent_id) {
        if (cur.parent_id === ancestorId) return true;
        cur = payload.nodes[cur.parent_id];
    }
    return false;
}

function flatVisible(): string[] {
    const out: string[] = [];
    const walk = (id: string): void => {
        const n = payload.nodes[id];
        if (!n) return;
        out.push(id);
        if (expanded.has(id)) for (const c of n.children) walk(c);
    };
    for (const r of payload.roots) walk(r);
    return out;
}

// ─── Top-level render ─────────────────────────────────────────────────────────
function renderAll(): void {
    app.replaceChildren();
    app.append(renderToolbar());
    const main = el('div', 'main');
    main.append(renderTree(), renderDocHost());
    app.append(main);
    if (!didFocusTree) { didFocusTree = true; queueMicrotask(focusTree); }
}

// ─── Reconcile (subsequent payloads) ─────────────────────────────────────────
function reconcile(): void {
    document.querySelector('.toolbar')?.replaceWith(renderToolbar());
    reconcileTree();
    // Feed the whole-doc editor the new settled doc (it ignores updates while the
    // user has unsettled local edits, so typing isn't clobbered) + the latest diffs.
    if (payload.doc && wholeEditor) {
        wholeEditor.setDoc(payload.doc);
        wholeEditor.setSuggestions(payload.suggestions ?? []);
        wholeEditor.setDeps(payload.deps ?? {});
    } else {
        document.querySelector('.doc-host')?.replaceWith(renderDocHost());
    }
}

function reconcileTree(): void {
    const tree = document.querySelector('.tree') as HTMLElement | null;
    if (!tree) return;
    const scroll = tree.scrollTop;
    const had = treeHasFocus();
    const next = renderTree();
    tree.replaceWith(next);
    next.scrollTop = scroll;
    if (had) next.focus({ preventScroll: true });
}

// ─── Toolbar ─────────────────────────────────────────────────────────────────
function renderToolbar(): HTMLElement {
    const t = el('div', 'toolbar');
    const p = el('div', 'path');
    p.append(el('span', 'dim', payload.rootName + ' / .codoc / '));
    p.append(document.createTextNode('tree.codoc'));
    t.append(p);

    const state = payload.status.state || 'in_sync';
    const pending = payload.status.pending || 0;
    const s = el('div', 'status ' + state);
    s.append(el('span', 'dot'));
    s.append(el('span', undefined, statusLabel(state, pending)));
    t.append(s);

    t.append(el('div', 'spacer'));

    const ids = payload.pendingEventIds;
    if (ids.length) {
        const accAll = el('button', 'toggle bulk', `✓ Accept all (${ids.length})`);
        accAll.onclick = () => { beginApplying(null); postVerdict(ids.slice(), true); };
        const rejAll = el('button', 'toggle bulk', `✗ Reject all (${ids.length})`);
        rejAll.onclick = () => { beginApplying(null); postVerdict(ids.slice(), false); };
        t.append(accAll, rejAll);
    }

    const btn = el('button', 'toggle', '⇄ text');
    btn.title = 'Open this file in the plain text editor';
    btn.onclick = () => vscode.postMessage({ kind: 'open-text' });
    t.append(btn);
    return t;
}

// ─── Tree pane (navigation) ────────────────────────────────────────────────
function renderTree(): HTMLElement {
    const wrap = el('div', 'tree');
    wrap.tabIndex = 0;
    if (payload.roots.length === 0) {
        wrap.append(el('div', 'empty', 'No features yet. Run `codoc init` to bootstrap the tree.'));
        return wrap;
    }
    for (const id of payload.roots) appendRow(wrap, id);
    return wrap;
}

function appendGhostRow(parent: HTMLElement, n: UINode): void {
    const row = el('div', 'row proposal ' + (n.proposalOp || 'add'));
    row.dataset.id = n.id;
    row.style.setProperty('--depth', String(n.depth));
    if (selectedId === n.id) row.classList.add('selected');
    // colour = direction (code-ahead; CSS), shape = kind via the lead glyph (U3 grammar)
    row.append(el('span', 'pglyph', kindGlyph(n.proposalOp || 'add')));
    const t = el('span', 'title ghost-title');
    t.textContent = n.title || '(untitled)';
    row.append(t);
    if (n.proposal?.tag) row.append(el('span', 'ghost-tag', n.proposal.tag));
    if (n.proposal) row.append(verdictButtons(n.proposal.eventId));
    row.onclick = () => setSelected(n.id, true);
    parent.append(row);
}

function appendRow(parent: HTMLElement, id: string): void {
    const n = payload.nodes[id];
    if (!n) return;
    if (n.isProposal) { appendGhostRow(parent, n); return; }

    const row = el('div', 'row');
    row.dataset.id = id;
    if (selectedId === id) row.classList.add('selected');
    if (n.retired) row.classList.add('retired');
    if (!n.realized) row.classList.add('unrealized');
    if (n.proposal?.op === 'amend') row.classList.add('has-amend');
    if (n.proposal?.op === 'retire') row.classList.add('has-retire');
    row.style.setProperty('--depth', String(n.depth));

    const handle = el('span', 'drag-handle', '⋮⋮');
    handle.draggable = true;
    handle.title = 'Drag to reparent under another feature';
    handle.ondragstart = ev => {
        dragSourceId = id;
        ev.dataTransfer!.effectAllowed = 'move';
        ev.dataTransfer!.setData('text/plain', id);
        document.body.classList.add('dragging');
        const ghost = row.cloneNode(true) as HTMLElement;
        ghost.style.cssText = 'position:absolute;top:-9999px;left:-9999px;opacity:.85;background:var(--vscode-editor-background)';
        document.body.append(ghost);
        try { ev.dataTransfer!.setDragImage(ghost, 10, 12); } catch { /* noop */ }
        setTimeout(() => ghost.remove(), 0);
        ev.stopPropagation();
    };
    handle.ondragend = () => {
        dragSourceId = null;
        document.body.classList.remove('dragging');
        document.querySelectorAll('.row.drop-target').forEach(r => r.classList.remove('drop-target'));
    };
    row.append(handle);

    const hasKids = n.children.length > 0;
    const isExp = expanded.has(id);
    const discCls = hasKids ? (isExp ? ' expanded' : ' collapsed') : ' leaf';
    const disc = el('span', 'disclosure' + discCls, hasKids ? (isExp ? '▾' : '▸') : '·');
    if (hasKids) {
        disc.title = isExp ? 'Collapse' : `Expand ${n.children.length} child${n.children.length === 1 ? '' : 'ren'}`;
        disc.onclick = ev => { ev.stopPropagation(); toggle(id); };
    }
    row.append(disc);

    const titleWrap = el('span', 'title', n.title || '(untitled)');
    titleWrap.title = 'Open in the document editor';
    // Titles are edited in the whole-doc editor now — double-click just scrolls there.
    titleWrap.ondblclick = ev => { ev.stopPropagation(); setSelected(id, true); };
    row.append(titleWrap);

    if (n.proposal?.op === 'amend' && n.proposal.title && n.proposal.title !== n.title) {
        row.append(el('span', 'amend-inline', '→ ' + n.proposal.title));
    }

    if (n.activeMode === 'write') row.append(el('span', 'badge active-write'));
    else if (n.activeMode === 'read') row.append(el('span', 'badge active-read'));
    if (!n.realized) row.append(el('span', 'badge unrealized'));
    if (n.proposal?.op === 'amend') row.append(el('span', 'badge amend'));
    if (n.proposal?.op === 'retire') row.append(el('span', 'badge retire'));

    if (n.refCount > 0) {
        const pill = el('span', 'refs-pill', n.refCount + (n.refCount === 1 ? ' ref' : ' refs'));
        pill.title = n.bindings.map(b => b.file + ' › ' + leafSym(b.symbol)).join('\n');
        pill.onclick = ev => { ev.stopPropagation(); setSelected(id, true); };
        row.append(pill);
    }

    if (n.proposal && (n.proposal.op === 'amend' || n.proposal.op === 'retire')) {
        row.append(verdictButtons(n.proposal.eventId));
    }

    row.onclick = () => { setSelected(id, true); focusTree(); };

    row.ondragover = ev => {
        if (!dragSourceId || dragSourceId === id || isDescendant(dragSourceId, id)) return;
        ev.preventDefault();
        ev.dataTransfer!.dropEffect = 'move';
        row.classList.add('drop-target');
    };
    row.ondragleave = ev => {
        if (!row.contains(ev.relatedTarget as Node)) row.classList.remove('drop-target');
    };
    row.ondrop = ev => {
        ev.preventDefault();
        row.classList.remove('drop-target');
        if (dragSourceId && dragSourceId !== id && !isDescendant(dragSourceId, id)) {
            vscode.postMessage({ kind: 'move', sourceId: dragSourceId, newParentId: id });
        }
        dragSourceId = null;
    };

    parent.append(row);
    if (isExp) for (const c of n.children) appendRow(parent, c);
}

// ─── Doc pane — ONE whole-doc editor over the entire tree ─────────────────────
function renderDocHost(): HTMLElement {
    const host = el('div', 'doc-host');
    if (wholeEditor) { wholeEditor.destroy(); wholeEditor = null; }
    if (!payload.doc) {
        host.append(el('div', 'doc empty', 'No features yet. Run `codoc init` to bootstrap the tree.'));
        return host;
    }
    wholeEditor = mountWholeDocEditor(host, {
        controller: authorController,
        getSymbols: () => payload.symbols ?? [],
        onSettle: doc => vscode.postMessage({ kind: 'doc-settle', doc }),
        onSuggest: suggestions => vscode.postMessage({ kind: 'suggest-create', suggestions }),
        onAccept: s => { if (s.eventId) { beginApplying(null); postVerdict([s.eventId], true); } },
        onReject: s => { if (s.eventId) { beginApplying(null); postVerdict([s.eventId], false); } },
        onWithdraw: s => vscode.postMessage({ kind: 'suggest-withdraw', id: s.id }),
        onApply: s => vscode.postMessage({ kind: 'suggest-apply', id: s.id }),
        onOpenBinding: (file, symbol) => vscode.postMessage({ kind: 'open-binding', file, symbol }),
        onActiveFeature: fid => {
            if (!fid) return;
            syncingFromEditor = true;
            setSelected(fid, false); // highlight the tree row, don't re-scroll the editor
            syncingFromEditor = false;
        },
    });
    wholeEditor.setDoc(payload.doc);
    wholeEditor.setSuggestions(payload.suggestions ?? []);
    wholeEditor.setDeps(payload.deps ?? {});
    return host;
}

// ─── Selection (tree ↔ editor) ───────────────────────────────────────────────
function setSelected(id: string | null, scrollDoc: boolean): void {
    selectedId = id;
    // Reveal a selected node's ancestors so its tree row exists and can be marked
    // .selected (cheap — only re-renders when an ancestor was actually collapsed).
    if (id) revealAncestors(id);
    document.querySelectorAll('.row.selected').forEach(r => r.classList.remove('selected'));
    if (!id) return;
    const rowEl = document.querySelector<HTMLElement>('.row[data-id="' + cssEsc(id) + '"]');
    if (rowEl) { rowEl.classList.add('selected'); rowEl.scrollIntoView({ block: 'nearest' }); }
    // Scroll the editor to this feature — unless the selection came from the editor's
    // own caret (avoid fighting it) or the id is a pending ghost (no live heading).
    if (scrollDoc && !syncingFromEditor && wholeEditor && id.startsWith('f-')) {
        wholeEditor.scrollToFeature(id);
    }
}

/** Expand every ancestor of `id` so its tree row becomes visible. */
function revealAncestors(id: string): void {
    let changed = false;
    let n: UINode | undefined = payload.nodes[id];
    while (n && n.parent_id) {
        if (!expanded.has(n.parent_id)) { expanded.add(n.parent_id); changed = true; }
        n = payload.nodes[n.parent_id];
    }
    if (changed) {
        const tree = document.querySelector('.tree');
        if (tree) { const next = renderTree(); tree.replaceWith(next); reapplyApplyingTo(next); }
    }
}

function toggle(id: string): void {
    if (expanded.has(id)) expanded.delete(id); else expanded.add(id);
    const tree = document.querySelector('.tree');
    if (tree) {
        const replacement = renderTree();
        tree.replaceWith(replacement);
        reapplyApplyingTo(replacement);
        (replacement as HTMLElement).focus({ preventScroll: true });
    }
}

// ─── Keyboard navigation (tree-focused) ──────────────────────────────────────
function moveCursor(delta: number): void {
    const visible = flatVisible();
    if (!visible.length) return;
    const idx = selectedId ? visible.indexOf(selectedId) : -1;
    const next = idx < 0 ? 0 : Math.max(0, Math.min(visible.length - 1, idx + delta));
    setSelected(visible[next], true);
}
function expandOrDescend(): void {
    if (!selectedId) return;
    const n = payload.nodes[selectedId];
    if (!n || n.children.length === 0) return;
    if (!expanded.has(selectedId)) toggle(selectedId);
    else setSelected(n.children[0], true);
}
function collapseOrAscend(): void {
    if (!selectedId) return;
    const n = payload.nodes[selectedId];
    if (!n) return;
    if (expanded.has(selectedId) && n.children.length > 0) toggle(selectedId);
    else if (n.parent_id) setSelected(n.parent_id, true);
}

/**
 * Keyboard nav is scoped to the tree pane: arrows / Space / Enter act only when
 * the tree is focused, so focus in the editor keeps native scrolling and we never
 * shadow VS Code or OS chords. No modified arrows; no bare Tab capture.
 */
function treeHasFocus(): boolean {
    const ae = document.activeElement;
    return !!(ae && ae.closest && ae.closest('.tree'));
}

document.addEventListener('keydown', ev => {
    const tag = (document.activeElement && document.activeElement.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA') return; // inputs own Esc/Enter
    if (!treeHasFocus()) return;                       // editor focus → native scroll
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return; // leave modified chords to VS Code
    switch (ev.key) {
        case 'ArrowDown': ev.preventDefault(); moveCursor(+1); return;
        case 'ArrowUp': ev.preventDefault(); moveCursor(-1); return;
        case 'ArrowRight': ev.preventDefault(); expandOrDescend(); return;
        case 'ArrowLeft': ev.preventDefault(); collapseOrAscend(); return;
        // Enter scrolls the editor to the selected feature (titles are edited there).
        case 'Enter': if (selectedId) { ev.preventDefault(); setSelected(selectedId, true); } return;
        case ' ': if (selectedId) { ev.preventDefault(); toggle(selectedId); } return;
    }
});

// ─── Message bus ────────────────────────────────────────────────────────────
window.addEventListener('message', ev => {
    const msg = ev.data as { kind: string; payload: DocPayload };
    if (msg.kind !== 'doc') return;
    if (msg.payload.rev < lastRev) return; // ignore stale posts
    lastRev = msg.payload.rev;
    payload = msg.payload;
    // endApplying MUST stay after the stale-rev guard — a stale (dropped) post must
    // not clear the optimistic applying state for a verdict still in flight.
    endApplying();

    if (selectedId && !payload.nodes[selectedId]) selectedId = null;
    for (const id of [...expanded]) if (!payload.nodes[id]) expanded.delete(id);
    if (firstPayload) {
        firstPayload = false;
        // Expand every parent so the whole tree is visible by default.
        for (const id of Object.keys(payload.nodes)) {
            if (payload.nodes[id].children.length) expanded.add(id);
        }
        if (selectedId == null) selectedId = payload.roots[0] ?? null;
    }

    if (!mounted) { mounted = true; renderAll(); } else { reconcile(); }
});

vscode.postMessage({ kind: 'ready' });
