/**
 * doc-view.ts — the bundled webview client for the Codoc Tree custom editor.
 *
 * Left: the feature tree (navigation). Right: one continuous documentation
 * article — every feature is a section, citations woven inline. Selecting a tree
 * node scrolls the article to its section; scrolling the article scroll-spies the
 * tree selection back. The .codoc text file stays the source of truth; edits flow
 * to the host as `edit-title` / `edit-description` / `move` / `verdict` messages.
 *
 * Phase 1+2: continuous doc, two-way scroll sync, inline editing, inline
 * proposals. Reconciliation/animation (FLIP, crossfade, streaming) layers on in
 * later phases — for now a payload triggers a full re-render.
 */

import './doc-view.css';
import { groupBindings } from '../state/doc-layout';
import type { DocPayload, UINode, WebviewMessage } from './protocol';
import type { DocSection, InlineRun, CrossRef } from '../state/doc-layout';

declare function acquireVsCodeApi(): { postMessage(msg: WebviewMessage): void };
const vscode = acquireVsCodeApi();

const EMPTY: DocPayload = {
    nodes: {}, roots: [], sections: [],
    status: { state: 'in_sync', pending: 0 },
    sync: { state: 'in_sync', pending: 0, activeWrite: [], activeRead: [], phase: {} },
    rootName: '', pendingEventIds: [], rev: 0,
};

let payload: DocPayload = EMPTY;
const expanded = new Set<string>();
let selectedId: string | null = null;
let editingTitle: string | null = null;   // feature id whose title is being edited
let editingDesc: string | null = null;    // feature id whose description is being edited
let firstPayload = true;
let dragSourceId: string | null = null;
let lastRev = -1;

// Scroll-sync guards.
let programmaticScroll = 0;                 // timestamp until which scroll-spy is muted
let observer: IntersectionObserver | null = null;
let didFocusTree = false;
let mounted = false;                         // first payload builds the shell; rest reconcile
const sectionById = new Map<string, HTMLElement>();
// Structural TOC rail: one tick per section, plus a marker that glides between
// ticks. Content snaps instantly to a section; the rail carries the "from→to"
// journey so navigation keeps spatial orientation without scrolling through text.
const tickById = new Map<string, HTMLElement>();
let tocMarker: HTMLElement | null = null;
let railOrderKey = '';                        // section-order fingerprint → rebuild rail only on change
const reduceMotion = (): boolean => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

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
// loop drains it and the sidecar refreshes. Without immediate feedback the click
// looks dead. beginApplying() disables the affected controls + shows "applying…";
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

/** After a DOM rebuild (section re-render, tree toggle/reveal) re-disable verdict
 *  controls inside `root` if a verdict is still in-flight (body.applying). Without
 *  this, freshly-built buttons carry no disabled state and silently become clickable
 *  again mid-apply — letting a duplicate verdict fire and flickering the row. */
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

// Word-level inline diff → fragment of d-same/d-del/d-ins spans (for AMEND).
function renderInlineDiff(oldStr: string, newStr: string): DocumentFragment {
    const a = String(oldStr).split(/(\s+)/), b = String(newStr).split(/(\s+)/);
    const n = a.length, m = b.length;
    const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
        dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    const frag = document.createDocumentFragment();
    const push = (txt: string, cls: string): void => { if (txt !== '') frag.append(el('span', cls, txt)); };
    let i = 0, j = 0;
    while (i < n && j < m) {
        if (a[i] === b[j]) { push(a[i], 'd-same'); i++; j++; }
        else if (dp[i + 1][j] >= dp[i][j + 1]) { push(a[i], 'd-del'); i++; }
        else { push(b[j], 'd-ins'); j++; }
    }
    while (i < n) push(a[i++], 'd-del');
    while (j < m) push(b[j++], 'd-ins');
    return frag;
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
    wireObserver();
    if (selectedId) syncToSection(selectedId, false);
    if (!didFocusTree) { didFocusTree = true; queueMicrotask(focusTree); }
}

// ─── Reconcile (subsequent payloads — keep scroll, animate only what changed) ─
function reconcile(): void {
    document.querySelector('.toolbar')?.replaceWith(renderToolbar());
    reconcileTree();
    reconcileDoc();
    if (selectedId) syncToSection(selectedId, false);
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

/** Topmost section currently in view — used to hold the reading position steady
 *  while sections above it change height. */
function pickScrollAnchor(doc: HTMLElement): { id: string; offset: number } | null {
    const top = doc.scrollTop;
    let best: { id: string; o: number } | null = null;
    for (const [id, e] of sectionById) {
        if (e.offsetTop + e.offsetHeight > top + 4 && (!best || e.offsetTop < best.o)) {
            best = { id, o: e.offsetTop };
        }
    }
    return best ? { id: best.id, offset: best.o - top } : null;
}

function reconcileDoc(): void {
    const doc = document.querySelector('.doc') as HTMLElement | null;
    const accent = document.getElementById('doc-accent');
    // Empty↔non-empty transitions or a missing host → rebuild the host wholesale.
    if (!doc || !accent || payload.sections.length === 0 || doc.classList.contains('empty')) {
        document.querySelector('.doc-host')?.replaceWith(renderDocHost());
        wireObserver();
        return;
    }

    const reduce = reduceMotion();
    const firstTop = new Map<string, number>();
    for (const [id, e] of sectionById) firstTop.set(id, e.getBoundingClientRect().top);
    const anchor = pickScrollAnchor(doc);
    const newSet = new Set(payload.sections.map(s => s.id));

    // Exits — features that vanished.
    for (const [id, e] of [...sectionById]) {
        if (!newSet.has(id)) { sectionById.delete(id); e.remove(); }
    }

    // Create / update (crossfade on content change) / reorder.
    let prev: ChildNode = accent;
    for (const sec of payload.sections) {
        const existing = sectionById.get(sec.id);
        const editingThis = editingDesc === sec.id || editingTitle === sec.id;
        const phaseChanged = existing && existing.dataset.phase !== (sec.flags.phase ?? '');
        const contentChanged = existing && existing.dataset.hash !== sec.contentHash;
        let node: HTMLElement;
        if (!existing) {
            node = renderSection(sec);
            if (!reduce) node.classList.add('entering');
            sectionById.set(sec.id, node);
            observer?.observe(node);
        } else if ((contentChanged || phaseChanged) && !editingThis) {
            const wasEditing = existing.dataset.phase === 'editing';
            node = renderSection(sec);
            sectionById.set(sec.id, node);
            observer?.observe(node);
            existing.replaceWith(node);
            if (!reduce) {
                if (wasEditing && sec.flags.phase !== 'editing') {
                    // skeleton → real content: staggered paragraph fade-up
                    node.querySelector('.prose')?.classList.add('revealing');
                } else if (contentChanged) {
                    node.classList.add('changed');
                }
            }
        } else {
            node = existing;                 // unchanged (or being edited) → keep DOM
            applyLiveFlags(node, sec);       // but reflect live agent activity
        }
        if (prev.nextSibling !== node) doc.insertBefore(node, prev.nextSibling);
        prev = node;
    }

    // Hold the reading position: keep the anchor section where it was on screen.
    if (anchor) {
        const a = sectionById.get(anchor.id);
        if (a) doc.scrollTop = a.offsetTop - anchor.offset;
    }

    // Rebuild the TOC rail only if the section set/order changed (marker is then
    // repositioned by the trailing syncToSection in reconcile()).
    reconcileRail();

    // FLIP: animate any section that moved to its new resting place.
    if (!reduce) {
        requestAnimationFrame(() => {
            for (const [id, node] of sectionById) {
                const before = firstTop.get(id);
                if (before == null) continue;          // freshly entered → handled by .entering
                const dy = before - node.getBoundingClientRect().top;
                if (Math.abs(dy) < 1) continue;
                node.style.transition = 'none';
                node.style.transform = `translateY(${dy}px)`;
                requestAnimationFrame(() => {
                    node.style.transition = 'transform 260ms var(--ease)';
                    node.style.transform = '';
                    node.addEventListener('transitionend', () => { node.style.transition = ''; node.style.transform = ''; }, { once: true });
                });
            }
        });
    }
}

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
    row.append(el('span', 'pglyph', n.proposalOp === 'move' ? '~' : '+'));
    const t = el('span', 'title ghost-title');
    t.textContent = (n.proposalOp === 'move' ? '→ ' : '') + (n.title || '(untitled)');
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
    const disc = el('span', 'disclosure' + (hasKids ? '' : ' empty'), hasKids ? (isExp ? '▾' : '▸') : '·');
    if (hasKids) disc.onclick = ev => { ev.stopPropagation(); toggle(id); };
    row.append(disc);

    const titleWrap = el('span', 'title', n.title || '(untitled)');
    titleWrap.title = 'Double-click to edit';
    titleWrap.ondblclick = ev => { ev.stopPropagation(); setSelected(id, true); startEditTitle(id); };
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

    row.onclick = () => { if (!editingTitle && !editingDesc) { setSelected(id, true); focusTree(); } };

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

// ─── Doc pane (the article) ────────────────────────────────────────────────
function renderDocHost(): HTMLElement {
    const host = el('div', 'doc-host');
    const doc = el('div', 'doc');
    sectionById.clear();
    if (payload.sections.length === 0) {
        doc.classList.add('empty');
        doc.textContent = 'No features yet.';
        host.append(doc);
        return host;
    }
    const accent = el('div', 'doc-accent');
    accent.id = 'doc-accent';
    doc.append(accent);
    for (const sec of payload.sections) {
        const node = renderSection(sec);
        sectionById.set(sec.id, node);
        doc.append(node);
    }
    host.append(doc);
    host.append(renderTocRail());
    return host;
}

// ─── TOC rail (structural minimap + gliding marker) ──────────────────────────
/** Build the right-edge rail: one depth-indented tick per section (in article
 *  order) and the absolutely-positioned marker. Cheap — a tiny node per section. */
function renderTocRail(): HTMLElement {
    const rail = el('div', 'toc-rail');
    tickById.clear();
    const marker = el('div', 'toc-marker');
    tocMarker = marker;
    rail.append(marker);
    for (const sec of payload.sections) {
        const tick = el('div', 'toc-tick');
        tick.dataset.id = sec.id;
        tick.style.setProperty('--d', String(Math.min(sec.level, 4)));
        if (sec.flags.isGhost) tick.classList.add('ghost', sec.flags.proposalOp === 'move' ? 'move' : 'add');
        if (sec.flags.retired) tick.classList.add('retired');
        if (!sec.flags.realized) tick.classList.add('unrealized');
        if (sec.proposal?.op === 'amend') tick.classList.add('has-amend');
        if (sec.proposal?.op === 'retire') tick.classList.add('has-retire');
        tick.title = sec.title || '(untitled)';
        tick.onclick = () => setSelected(sec.id, true);
        tickById.set(sec.id, tick);
        rail.append(tick);
    }
    railOrderKey = payload.sections.map(s => s.id).join('\x00');
    return rail;
}

/** Glide the marker to a section's tick and highlight it. Position is the tick's
 *  own offset within the rail (not doc scroll) — so the journey is structural. */
function moveTocMarker(id: string): void {
    const tick = tickById.get(id);
    if (!tick || !tocMarker) return;
    for (const [tid, t] of tickById) t.classList.toggle('active', tid === id);
    // Center the (fixed-height) marker on the tick; CSS margin-top offsets half.
    tocMarker.style.transform = `translateY(${tick.offsetTop + tick.offsetHeight / 2}px)`;
    tocMarker.style.opacity = '1';
}

/** Rebuild the rail only when the section set / order changed — otherwise keep
 *  the existing ticks (and the marker's glide state) untouched. */
function reconcileRail(): void {
    const host = document.querySelector('.doc-host');
    if (!host) return;
    if (payload.sections.map(s => s.id).join('\x00') === railOrderKey) return;
    const old = host.querySelector('.toc-rail');
    const next = renderTocRail();
    if (old) old.replaceWith(next); else host.append(next);
}

function renderRuns(runs: InlineRun[]): DocumentFragment {
    const frag = document.createDocumentFragment();
    for (const run of runs) {
        if (run.t === 'text') {
            frag.append(document.createTextNode(run.s));
        } else {
            const chip = el('span', 'cite', run.label);
            chip.title = run.file + (run.symbol ? ' › ' + run.symbol : '');
            chip.onclick = ev => {
                ev.stopPropagation();
                vscode.postMessage({ kind: 'open-binding', file: run.file, symbol: run.symbol ?? '' });
            };
            frag.append(chip);
        }
    }
    return frag;
}

function renderProse(sec: DocSection): HTMLElement {
    // Inline AMEND description diff (old struck → new added).
    if (sec.proposal?.op === 'amend' && sec.proposal.description != null) {
        const dd = el('div', 'prose');
        const raw = sec.blocks.map(b => b.map(r => r.t === 'text' ? r.s : `[${r.label}]`).join('')).join('\n\n');
        dd.append(renderInlineDiff(raw, sec.proposal.description));
        dd.title = 'Proposed description change · double-click to edit the live text';
        if (!sec.flags.isGhost) dd.ondblclick = () => startEditDesc(sec.id);
        return dd;
    }
    if (sec.blocks.length === 0) {
        const dd = el('div', 'prose empty', sec.flags.isGhost
            ? 'New feature — accept to add it to the tree.'
            : 'No description — double-click to add one.');
        if (!sec.flags.isGhost) dd.ondblclick = () => startEditDesc(sec.id);
        return dd;
    }
    const dd = el('div', 'prose');
    for (const block of sec.blocks) {
        const p = el('p');
        p.append(renderRuns(block));
        dd.append(p);
    }
    if (!sec.flags.isGhost) dd.ondblclick = () => startEditDesc(sec.id);
    return dd;
}

const RAIL_LIMIT = 8;

function renderRail(bindings: { file: string; symbol: string }[]): HTMLElement {
    const wrap = el('div', 'rail');
    for (const g of groupBindings(bindings)) {
        const grp = el('div', 'rail-group');
        const head = el('div', 'rail-file');
        head.append(el('span', 'fname', g.file));
        head.append(el('span', 'fcount', String(g.items.length)));
        head.title = 'Open ' + g.file;
        head.onclick = () => vscode.postMessage({ kind: 'open-binding', file: g.file, symbol: '' });
        grp.append(head);

        const list = el('div', 'rail-syms');
        const makeRow = (it: { symbol: string; file: string; label: string; depth: number }): HTMLElement => {
            const r = el('div', 'rsym');
            r.style.setProperty('--d', String(Math.min(it.depth, 3)));
            r.textContent = it.label;
            r.title = it.symbol;
            r.onclick = () => vscode.postMessage({ kind: 'open-binding', file: it.file, symbol: it.symbol });
            return r;
        };
        g.items.slice(0, RAIL_LIMIT).forEach(it => list.append(makeRow(it)));
        if (g.items.length > RAIL_LIMIT) {
            const more = el('div', 'rsym more', `+${g.items.length - RAIL_LIMIT} more`);
            more.onclick = () => {
                more.remove();
                g.items.slice(RAIL_LIMIT).forEach(it => list.append(makeRow(it)));
            };
            list.append(more);
        }
        grp.append(list);
        wrap.append(grp);
    }
    return wrap;
}

function renderXrefs(refs: CrossRef[]): HTMLElement {
    const wrap = el('div', 'xrefs');
    wrap.append(el('span', 'xlabel', 'see also'));
    const TOP = 4;
    const shown = refs.slice(0, TOP);
    for (const r of shown) {
        const x = el('span', 'xref');
        x.append(el('span', 'dir', r.rel === 'depends' ? '↳' : '↰'));
        x.append(document.createTextNode(r.toTitle || '(untitled)'));
        x.title = `${r.rel === 'depends' ? 'depends on' : 'used by'} · ${r.kinds.join(', ')} · weight ${r.weight}`;
        x.onclick = () => setSelected(r.toId, true);
        wrap.append(x);
    }
    if (refs.length > TOP) {
        const more = el('span', 'xref-more', `+${refs.length - TOP} more`);
        more.onclick = () => {
            more.remove();
            for (const r of refs.slice(TOP)) {
                const x = el('span', 'xref');
                x.append(el('span', 'dir', r.rel === 'depends' ? '↳' : '↰'));
                x.append(document.createTextNode(r.toTitle || '(untitled)'));
                x.onclick = () => setSelected(r.toId, true);
                wrap.append(x);
            }
        };
        wrap.append(more);
    }
    return wrap;
}

function renderSection(sec: DocSection): HTMLElement {
    const s = el('div', 'section');
    s.dataset.id = sec.id;
    s.dataset.level = String(Math.min(sec.level, 4));
    if (sec.flags.retired) s.classList.add('retired');
    if (!sec.flags.realized) s.classList.add('unrealized');
    if (sec.flags.isGhost) s.classList.add('ghost', sec.flags.proposalOp === 'move' ? 'move' : 'add');
    if (sec.proposal?.op === 'amend') s.classList.add('has-amend');

    // Heading (editable for live features).
    if (editingTitle === sec.id) {
        const inp = document.createElement('input');
        inp.className = 't-edit-big';
        inp.value = sec.title;
        inp.onkeydown = ev => {
            ev.stopPropagation();
            if (ev.key === 'Enter') { ev.preventDefault(); commitTitle(sec.id, inp.value); }
            else if (ev.key === 'Escape') { ev.preventDefault(); cancelTitle(); }
        };
        inp.onblur = () => { if (editingTitle === sec.id) commitTitle(sec.id, inp.value); };
        s.append(inp);
        queueMicrotask(() => { inp.focus(); inp.select(); });
    } else {
        const h = el('h2', 'h');
        if (sec.flags.isGhost && sec.flags.proposalOp === 'move') h.append(el('span', 'crumb', '→ moved'));
        h.append(document.createTextNode(sec.title || '(untitled)'));
        if (!sec.flags.isGhost) {
            h.title = 'Double-click to edit the title';
            h.ondblclick = () => { setSelected(sec.id, false); startEditTitle(sec.id); };
            const pencil = el('button', 'edit-pencil', '✎');
            pencil.title = 'Edit description';
            pencil.onclick = ev => { ev.stopPropagation(); setSelected(sec.id, false); startEditDesc(sec.id); };
            h.append(pencil);
        }
        s.append(h);
    }

    // Meta pills.
    const meta = el('div', 'meta');
    if (sec.bindings.length > 0) meta.append(el('span', 'pill', sec.bindings.length + ' binding' + (sec.bindings.length === 1 ? '' : 's')));
    if (!sec.flags.realized) meta.append(el('span', 'pill plan', 'unrealized'));
    if (sec.proposal?.op === 'amend') meta.append(el('span', 'pill amend', 'amend pending'));
    if (sec.proposal?.op === 'retire') meta.append(el('span', 'pill retire', 'retire pending'));
    if (meta.children.length) s.append(meta);

    // AMEND title diff.
    if (sec.proposal?.op === 'amend' && sec.proposal.title && sec.proposal.title !== sec.title) {
        const dx = el('div', 'amend-title');
        dx.append(el('span', 'lbl', 'title'));
        dx.append(el('span', 'old', sec.title));
        dx.append(el('span', 'arrow', '→'));
        dx.append(el('span', 'new', sec.proposal.title));
        s.append(dx);
    }

    // Prose (or inline desc editor, or skeleton while the agent reworks it).
    if (editingDesc === sec.id) {
        s.append(renderDescEditor(sec));
    } else if (sec.flags.phase === 'editing' && !sec.flags.isGhost) {
        s.classList.add('streaming');
        s.append(renderSkeleton());
    } else {
        s.append(renderProse(sec));
    }

    // Binding rail — grouped by file (filename once), symbols ordered like a
    // structural minimap, collapsed past a threshold.
    if (sec.bindings.length > 0) s.append(renderRail(sec.bindings));

    // Cross-feature "see also".
    if (sec.crossRefs.length > 0) s.append(renderXrefs(sec.crossRefs));

    // Inline verdict for a proposal on this section.
    if (sec.proposal) {
        if (sec.proposal.op === 'retire') {
            s.append(el('div', 'retire-note', 'Retire proposed — accepting untracks this feature and detaches its bindings. The code itself is kept; to remove it, edit tree.codoc and mark the node with ~.'));
        }
        const acts = el('div', 'inline-verdict');
        if (sec.proposal.tag) acts.append(el('span', 'iv-tag', sec.proposal.tag));
        const rej = el('button', undefined, 'Reject');
        rej.onclick = () => { beginApplying(acts); postVerdict([sec.proposal!.eventId], false); };
        const acc = el('button', 'primary', 'Accept');
        acc.onclick = () => { beginApplying(acts); postVerdict([sec.proposal!.eventId], true); };
        acts.append(rej, acc);
        s.append(acts);
    }

    s.onclick = () => { if (!editingTitle && !editingDesc) setSelected(sec.id, false); };
    s.dataset.hash = sec.contentHash;
    s.dataset.phase = sec.flags.phase ?? '';
    applyLiveFlags(s, sec);
    return s;
}

function renderSkeleton(): HTMLElement {
    const sk = el('div', 'skeleton');
    sk.append(el('div', 'bar'), el('div', 'bar'), el('div', 'bar'));
    return sk;
}

/** Toggle live agent-activity classes on an existing section node without a
 *  content rebuild (so the reconciler can update "editing/reading now" cheaply).
 *  contentHash deliberately excludes active state, so these never crossfade. */
function applyLiveFlags(node: HTMLElement, sec: DocSection): void {
    node.classList.toggle('active-write', sec.flags.activeMode === 'write');
    node.classList.toggle('active-read', sec.flags.activeMode === 'read');
}

function renderDescEditor(sec: DocSection): HTMLElement {
    const wrap = el('div');
    const ta = document.createElement('textarea');
    ta.className = 'd-edit';
    ta.value = sec.raw;     // exact stored text → lossless round-trip
    ta.onkeydown = ev => {
        ev.stopPropagation();
        if (ev.key === 'Escape') { ev.preventDefault(); cancelDesc(); }
        else if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); commitDesc(sec.id, ta.value); }
    };
    ta.onblur = () => { if (editingDesc === sec.id) commitDesc(sec.id, ta.value); };
    wrap.append(ta);
    const hint = el('div', 'edit-hint');
    hint.append(document.createTextNode('Commit with '));
    hint.append(el('kbd', undefined, '⌘'));
    hint.append(document.createTextNode(' '));
    hint.append(el('kbd', undefined, 'Enter'));
    hint.append(document.createTextNode('  ·  Cancel with '));
    hint.append(el('kbd', undefined, 'Esc'));
    wrap.append(hint);
    queueMicrotask(() => { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); });
    return wrap;
}

// ─── Selection + two-way scroll sync ────────────────────────────────────────
function setSelected(id: string | null, scrollDoc: boolean): void {
    selectedId = id;
    // Always reveal a selected node's ancestors so its tree row exists and can be
    // marked .selected — whether selection came from a tree click, keyboard, an
    // xref jump, a doc-heading click, or scroll-spy. revealAncestors only re-renders
    // when an ancestor was actually collapsed, so scroll-spy within an already-open
    // subtree stays cheap (no per-tick re-render).
    if (id) revealAncestors(id);
    document.querySelectorAll('.row.selected').forEach(r => r.classList.remove('selected'));
    if (id) {
        const rowEl = document.querySelector<HTMLElement>('.row[data-id="' + cssEsc(id) + '"]');
        if (rowEl) { rowEl.classList.add('selected'); rowEl.scrollIntoView({ block: 'nearest' }); }
        syncToSection(id, scrollDoc);
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

/** Mark a section active: glide the TOC marker + accent bar to it, and — when
 *  navigating — SNAP the doc to it (no smooth scroll-through) with a brief
 *  landing cue. The journey is carried by the gliding rail marker, not by
 *  scrolling every intermediate section past the eye. */
function syncToSection(id: string, scrollDoc: boolean): void {
    const sec = sectionById.get(id);
    const accent = document.getElementById('doc-accent');
    moveTocMarker(id);
    if (!sec || !accent) return;
    accent.style.top = sec.offsetTop + 'px';
    accent.style.height = sec.offsetHeight + 'px';
    accent.style.opacity = '1';
    if (scrollDoc) {
        // Instant snap (mute scroll-spy just long enough to not re-fight the target).
        programmaticScroll = Date.now() + 120;
        sec.scrollIntoView({ block: 'start', behavior: 'auto' });
        if (!reduceMotion()) {
            const h = sec.querySelector('.h') as HTMLElement | null;
            if (h) {
                h.classList.remove('landed');
                void h.offsetWidth;                       // reflow → restart the cue on re-landing
                h.classList.add('landed');
                h.addEventListener('animationend', () => h.classList.remove('landed'), { once: true });
            }
        }
    }
}

function wireObserver(): void {
    observer?.disconnect();
    const doc = document.querySelector('.doc');
    if (!doc) return;
    observer = new IntersectionObserver(entries => {
        if (Date.now() < programmaticScroll) return;
        // Choose the topmost section currently crossing the middle band.
        let best: { id: string; top: number } | null = null;
        for (const e of entries) {
            if (!e.isIntersecting) continue;
            const id = (e.target as HTMLElement).dataset.id;
            if (!id) continue;
            const top = e.boundingClientRect.top;
            if (!best || top < best.top) best = { id, top };
        }
        if (best && best.id !== selectedId) {
            selectedId = best.id;
            document.querySelectorAll('.row.selected').forEach(r => r.classList.remove('selected'));
            const rowEl = document.querySelector<HTMLElement>('.row[data-id="' + cssEsc(best.id) + '"]');
            if (rowEl) { rowEl.classList.add('selected'); rowEl.scrollIntoView({ block: 'nearest' }); }
            syncToSection(best.id, false);
        }
    }, { root: doc, rootMargin: '-45% 0px -45% 0px', threshold: 0 });
    for (const sec of sectionById.values()) observer.observe(sec);
}

// ─── State transitions ──────────────────────────────────────────────────────
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

function startEditTitle(id: string): void {
    editingDesc = null;
    editingTitle = id;
    rerenderSection(id);
}
function cancelTitle(): void { const id = editingTitle; editingTitle = null; if (id) rerenderSection(id); }
function commitTitle(id: string, newTitle: string): void {
    if (editingTitle !== id) return;
    editingTitle = null;
    const trimmed = newTitle.trim();
    const node = payload.nodes[id];
    const current = node ? node.title : '';
    if (trimmed && trimmed !== current) {
        if (node) node.title = trimmed;
        const sec = payload.sections.find(x => x.id === id);
        if (sec) sec.title = trimmed;
        vscode.postMessage({ kind: 'edit-title', featureId: id, newTitle: trimmed });
    }
    rerenderSection(id);
    restoreTreeTitle(id);
}
function restoreTreeTitle(id: string): void {
    const rowEl = document.querySelector('.row[data-id="' + cssEsc(id) + '"] .title');
    if (rowEl) rowEl.textContent = payload.nodes[id]?.title || '(untitled)';
}

function startEditDesc(id: string): void { editingTitle = null; editingDesc = id; rerenderSection(id); }
function cancelDesc(): void { const id = editingDesc; editingDesc = null; if (id) rerenderSection(id); }
function commitDesc(id: string, newDesc: string): void {
    if (editingDesc !== id) return;
    editingDesc = null;
    vscode.postMessage({ kind: 'edit-description', featureId: id, newDescription: newDesc });
    rerenderSection(id);
}

/** Re-render just one section in place (keeps scroll position stable). */
function rerenderSection(id: string): void {
    const old = sectionById.get(id);
    const sec = payload.sections.find(x => x.id === id);
    if (!old || !sec) return;
    const next = renderSection(sec);
    sectionById.set(id, next);
    old.replaceWith(next);
    reapplyApplyingTo(next);
    observer?.observe(next);
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
 * Keyboard nav is scoped to the tree pane on purpose: arrows / Space / Enter
 * only act when the tree is focused, so focus in the doc keeps native scrolling
 * (Space = page down, arrows = line scroll) and we never shadow VS Code or OS
 * chords. No modified arrows (those collide with editor word/line nav); no bare
 * Tab capture (focus traversal). Escape always cancels an in-flight edit.
 */
function treeHasFocus(): boolean {
    const ae = document.activeElement;
    return !!(ae && ae.closest && ae.closest('.tree'));
}

document.addEventListener('keydown', ev => {
    const tag = (document.activeElement && document.activeElement.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;   // inputs own Esc/Enter
    if (ev.key === 'Escape') {
        if (editingTitle) { ev.preventDefault(); cancelTitle(); }
        else if (editingDesc) { ev.preventDefault(); cancelDesc(); }
        return;
    }
    if (!treeHasFocus()) return;                         // doc focus → native scroll
    if (ev.metaKey || ev.ctrlKey || ev.altKey) {         // leave modified chords to VS Code…
        if (ev.key === 'Enter' && selectedId) { ev.preventDefault(); startEditDesc(selectedId); }
        return;
    }
    switch (ev.key) {
        case 'ArrowDown': ev.preventDefault(); moveCursor(+1); return;
        case 'ArrowUp': ev.preventDefault(); moveCursor(-1); return;
        case 'ArrowRight': ev.preventDefault(); expandOrDescend(); return;
        case 'ArrowLeft': ev.preventDefault(); collapseOrAscend(); return;
        case 'Enter': if (selectedId) { ev.preventDefault(); startEditTitle(selectedId); } return;
        case ' ': if (selectedId) { ev.preventDefault(); toggle(selectedId); } return;
    }
});

// ─── Message bus ────────────────────────────────────────────────────────────
window.addEventListener('message', ev => {
    const msg = ev.data as { kind: string; payload: DocPayload };
    if (msg.kind !== 'doc') return;
    if (msg.payload.rev < lastRev) return;   // ignore stale posts
    lastRev = msg.payload.rev;
    payload = msg.payload;
    // endApplying MUST stay after the stale-rev guard above — a stale (dropped)
    // post must not clear the optimistic applying state for a verdict still in flight.
    endApplying();   // authoritative state arrived → clear optimistic "applying…"

    if (selectedId && !payload.nodes[selectedId] && !payload.sections.some(s => s.id === selectedId)) selectedId = null;
    for (const id of [...expanded]) if (!payload.nodes[id]) expanded.delete(id);
    if (firstPayload) {
        firstPayload = false;
        for (const r of payload.roots) expanded.add(r);
        if (selectedId == null) selectedId = payload.roots[0] ?? payload.sections[0]?.id ?? null;
    }
    // Preserve an in-flight inline edit across reflections elsewhere; only drop
    // it if the edited feature itself disappeared from the payload.
    const stillThere = (id: string | null): boolean => !!id && payload.sections.some(s => s.id === id);
    if (!stillThere(editingTitle)) editingTitle = null;
    if (!stillThere(editingDesc)) editingDesc = null;

    if (!mounted) { mounted = true; renderAll(); } else { reconcile(); }
});

vscode.postMessage({ kind: 'ready' });
