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
import { icon, iconMaskDataUri } from './icons';
import { tweenScrollTop, TweenController, popLanded, spinReject, saveShimmer, launchPlane } from './motion';
import { shouldCenter, centerScrollTarget } from './tree-center';
import { BridgeDebounce } from '../state/bridge';
import { deriveAgentPresences, type PresencePhase } from '../state/presence';
import { PresenceLayer } from './presence-layer';
import { CommandPalette } from './palette-view';
import type { PaletteContext, PaletteItem } from './palette';
import { serializeUiState, deserializeUiState, UiState } from './ui-state';
import type { DocPayload, UINode, WebviewMessage, WebviewPrefs } from './protocol';
import { acquireHostApi, isVsCodeHost } from './host-bridge';

// One transport seam for both homes (U2): the real VS Code host API, or — in a
// standalone browser served by `codoc serve` — a network shim that POSTs commands
// to the hub and re-dispatches SSE payloads as the same `message` events the host
// posts, so everything below is identical in either home (see ./host-bridge).
const vscode = acquireHostApi();

const EMPTY: DocPayload = {
    nodes: {}, roots: [],
    status: { state: 'in_sync', pending: 0 },
    sync: { state: 'in_sync', pending: 0, activeWrite: [], activeRead: [], phase: {} },
    rootName: '', pendingEventIds: [], rev: 0,
};

let payload: DocPayload = EMPTY;
// Per-workspace webview prefs (B-U2): overview dismiss + glance toggle. Seeded from
// the host payload (workspaceState) on the first doc, then mutated optimistically and
// persisted back via a `set-pref` message. Local copy avoids a round-trip flicker.
let prefs: WebviewPrefs = { glance: false };
let prefsSeeded = false;
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
// Features awaiting AI realization (the daemon's hold set) — drives the calm
// "being realized" badge on tree rows. Mirrors the editor's heading badge.
let awaitingAI = new Set<string>();
// Held drafts (U3/U4): code-implying edits recorded & staged but NOT yet handed off.
// `awaitingAI` minus `draftSet` = handed-off (sent). Partitions the tree-row badge into
// "captured" (draft) vs "pending" (sent), matching the doc-pane decoration families.
let draftSet = new Set<string>();
// Features whose realization DIVERGED (U5) — the agent changed them beyond the
// feature you edited; flagged "review what the AI did" alongside the surfaced
// proposal. fid → reason ("scope").
let divergent: Record<string, string> = {};
// Focus mode (the dependency spotlight, redesigned 2026-06): DEFAULT-ON and caret-driven.
// As the caret / scroll-spy moves to a feature, the tree gently dims every row outside that
// feature's dependency neighbourhood (its reads + used-by, plus the ancestor path so the
// tree still reads structurally). It is pure OPACITY — no recolour (a hue read too strong) —
// related rows stay 1.0, the rest ease to 0.7. `focusState.related` is the lit set for the
// current focus (null = nothing dimmed); `focusMode` is the (default-on) toggle.
let focusState: { fid: string; related: Set<string> } | null = null;
let focusMode = true;
let firstPayload = true;
// Continuous pane resize: the nav-tree column width (px), dragged via the gutter and
// persisted in UiState so a compact tree survives reload. Clamped on apply.
let treeWidth = 0;            // 0 = use the CSS default until restored / dragged
let dragSourceId: string | null = null;
let lastRev = -1;
let didFocusTree = false;
let mounted = false; // first payload builds the shell; the rest reconcile

// Tree auto-center (U2): the active feature (scroll-spy) eases to the vertical centre of the
// tree pane. ONLY the scroll source re-centers (KTD2 — caret moves just highlight); a manual
// tree scroll/wheel opens a suppress window so the two don't fight; keyboard nav + tree
// re-renders cancel any in-flight tween.
let centerTween: TweenController | null = null;
let treeTweenActive = false;      // true while OUR tween writes scrollTop → ignore its scroll events
let programmaticTreeScroll = false; // true while we restore scrollTop on a reconcile
let suppressAutoCenter = false;   // true briefly after a real manual tree scroll/wheel
let suppressTimer = 0;

// UI-state persistence (U5): selection/expansion/caret/scroll survive close→reopen and a full
// reload via vscode.getState/setState. Captured debounced (~400ms); restored on the first
// payload (sync seed before first paint) + after the editor mounts (caret/scroll).
let pendingRestore: UiState | null = null;
let persistTimer = 0;

// ── Cross-surface bridge (P2 / §A) ───────────────────────────────────────────
// Doc→code: an edit inside a bound feature opens its code Beside, debounced 180 ms so a fast
// typist doesn't thrash the split (§A.1/§A.5). The last fid we opened the bridge for, so a
// caret-leave (active feature changes with no edit) clears the code-side highlight via
// `bridge-dim` but never closes the pane (opening is eager, closing is the user's call, §A.1).
const bridgeDebounce = new BridgeDebounce(180,
    (fn, ms) => window.setTimeout(fn, ms), id => clearTimeout(id));
let bridgeFid: string | null = null;

// ── Agent presence (P3 / §B) ──────────────────────────────────────────────────
// A floating avatar glides to the feature an agent is touching, whispering what it does.
// Driven off the already-plumbed sync.phase / activeRead / sync.realize (no new backend).
const presence = new PresenceLayer({
    docHost: () => document.querySelector<HTMLElement>('.doc-host'),
    docSurface: () => document.querySelector<HTMLElement>('.ce-whole-surface'),
    treePane: () => document.querySelector<HTMLElement>('.tree'),
    scrollToFeature: fid => { setSelected(fid, true); },
});

/** Push the latest sync signal into the presence layer. The activity schema carries no
 *  per-agent identity, so all live features attribute to the single keyless-Claude default
 *  (presence.ts); a future per-agent signal drops in there without touching this. */
function updatePresence(): void {
    const phase = (payload.sync.phase ?? {}) as Record<string, PresencePhase>;
    presence.update(deriveAgentPresences(phase, payload.sync.activeRead ?? []), payload.sync.realize);
}

// ── Command palette (P4 / §D) ──────────────────────────────────────────────────
// The last few features the user selected (most-recent first) — the §D.3 welcome dashboard's
// "Recent" list. In-memory (resets on reload; the spec only needs it useful with zero typing).
const recentFids: string[] = [];
function noteRecent(fid: string | null): void {
    if (!fid || !fid.startsWith('f-')) return;
    const i = recentFids.indexOf(fid);
    if (i >= 0) recentFids.splice(i, 1);
    recentFids.unshift(fid);
    if (recentFids.length > 8) recentFids.length = 8;
}

const palette = new CommandPalette({
    context: () => buildPaletteContext(),
    recentFids: () => recentFids,
    run: (item, shift) => runPaletteAction(item, shift),
});

/** A DOM-free snapshot of the live state the palette reads (§D.2). */
function buildPaletteContext(): PaletteContext {
    const features = payload.roots.length
        ? Object.values(payload.nodes)
            .filter(n => !n.isProposal && n.id.startsWith('f-'))
            .map(n => ({
                id: n.id,
                title: n.title,
                bound: (n.bindings?.length ?? 0) > 0,
                detail: n.bindings?.length
                    ? `${n.bindings.length} ref${n.bindings.length === 1 ? '' : 's'} · ${n.bindings[0].file}`
                    : undefined,
            }))
        : [];
    const pendingFids = Object.values(payload.nodes).filter(n => n.proposal && !n.isProposal).map(n => n.id);
    const active = selectedId && payload.nodes[selectedId] ? payload.nodes[selectedId] : null;
    return {
        features,
        driftFids: [],   // drift isn't in the webview payload (host-side decoration only)
        pendingFids,
        divergentFids: Object.keys(divergent),
        activeFid: active?.id ?? null,
        activeTitle: active?.title ?? '',
        activeHeld: !!(active && awaitingAI.has(active.id)),
        activeBound: !!(active && (active.bindings?.length ?? 0) > 0),
        pendingEventCount: payload.pendingEventIds.length,
        draftCount: (payload.drafts ?? []).length,
        caretInProposal: !!(active && active.proposal),
        glance: prefs.glance,
        featureCount: features.length,
    };
}

/** Interpret a chosen palette item (§D.2/§D.4). Reuses existing callbacks + postMessage kinds;
 *  no new host messages (`bridge-open` is shared with the bridge). `shift` = ⇧↵ secondary. */
function runPaletteAction(item: PaletteItem, shift: boolean): void {
    switch (item.action) {
        case 'goto':
            if (item.arg) {
                if (shift) { bridgeFid = item.arg; vscode.postMessage({ kind: 'bridge-open', fid: item.arg }); }
                setSelected(item.arg, true);
            }
            return;
        case 'open-code':
            if (item.arg) { bridgeFid = item.arg; vscode.postMessage({ kind: 'bridge-open', fid: item.arg }); }
            return;
        case 'accept-all': if (payload.pendingEventIds.length) { beginApplying(null); postVerdict(payload.pendingEventIds.slice(), true); } return;
        case 'reject-all': if (payload.pendingEventIds.length) { beginApplying(null); postVerdict(payload.pendingEventIds.slice(), false); } return;
        case 'accept-cursor': { const id = selectedId && payload.nodes[selectedId]?.proposal?.eventId; if (id) { beginApplying(null); postVerdict([id], true); } return; }
        case 'reject-cursor': { const id = selectedId && payload.nodes[selectedId]?.proposal?.eventId; if (id) { beginApplying(null); postVerdict([id], false); } return; }
        case 'hand-off': triggerCommit(); return;
        case 'withdraw': if (item.arg) vscode.postMessage({ kind: 'withdraw-realization', featureId: item.arg }); return;
        case 'toggle-glance': setPref('glance', !prefs.glance); applyGlance(); rerenderToolbar(); return;
        case 'collapse-all': for (const id of [...expanded]) expanded.delete(id); rerenderTree(); persistUiState(); return;
        case 'expand-all': for (const id of Object.keys(payload.nodes)) if (payload.nodes[id].children.length) expanded.add(id); rerenderTree(); persistUiState(); return;
        case 'create': if (item.arg) wholeEditor?.createFeature(item.arg); return;
        case 'noop': return;
    }
}

function focusTree(): void {
    (document.querySelector('.tree') as HTMLElement | null)?.focus({ preventScroll: true });
}

const app = document.getElementById('app')!;

// Standalone home (codoc serve in a browser): no VS Code theme vars are injected, so
// tag the body to apply codoc's own calm paper-white "Ollama" palette as the default
// token values. Inside VS Code the host's --vscode-* vars win and the editor stays
// theme-aware — this class is never added there.
if (!isVsCodeHost()) document.body.classList.add('codoc-standalone');

// ─── DOM helpers ────────────────────────────────────────────────────────────
function el<K extends keyof HTMLElementTagNameMap>(
    tag: K, cls?: string | null, text?: string,
): HTMLElementTagNameMap[K] {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
}

// leafSym moved to suggestion-decorations.ts (used by the threads line — U4)

function cssEsc(s: string): string {
    return (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/["\\]/g, '\\$&');
}

/** Handed-off features (staged & sent) = the daemon hold set MINUS the still-drafted
 *  (recorded, not yet sent) ones. The two partition the hold set across the
 *  captured (drafts) and pending (handed-off) lifecycle phases. */
function handedOff(p: DocPayload): string[] {
    const d = new Set(p.drafts ?? []);
    return (p.awaitingAI ?? []).filter(f => !d.has(f));
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

// ── webview prefs (B-U2: overview dismiss + glance) ──────────────────────────
function setPref(pref: 'glance', value: boolean): void {
    prefs = { ...prefs, [pref]: value };
    vscode.postMessage({ kind: 'set-pref', pref, value }); // persist in workspaceState
}

/** Push the current glance + pitch state into the editor (decoration only). */
function applyGlance(): void {
    document.body.classList.toggle('glance', prefs.glance);
    if (!wholeEditor) return;
    wholeEditor.setPitches(payload.pitches ?? {});
    wholeEditor.setGlance(prefs.glance);
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
    const acc = el('button', 'v-accept'); acc.title = 'Accept';
    acc.append(icon('check-circle')); // §C.1: filled check = landed
    acc.onclick = ev => {
        ev.stopPropagation();
        // §C.3 accept: an optimistic "landed" pop on the glyph + a green row flash. The
        // authoritative removal arrives async (or the 5s applyingTimer reverts) — we never
        // fake-collapse the row here, the next payload drops it once the verdict drains.
        popLanded(acc.querySelector<HTMLElement>('.ce-icon'));
        flashAccept(wrap.closest<HTMLElement>('.row'));
        beginApplying(wrap); postVerdict([eventId], true);
    };
    const rej = el('button', 'v-reject'); rej.title = 'Reject';
    rej.append(icon('x-circle'));     // §C.1: x-circle = dismissed
    rej.onclick = ev => {
        ev.stopPropagation();
        spinReject(rej.querySelector<HTMLElement>('.ce-icon')); // §C.3: quieter than accept
        beginApplying(wrap); postVerdict([eventId], false);
    };
    wrap.append(rej, acc);
    return wrap;
}

/** §C.3 accept: a brief (1px-flash-grade) green row flash ("this is now yours") — held for
 *  --dur-fast (120 ms), not a lingering wash. A pure CSS class toggle, so reduced motion (the
 *  blanket gate) zeroes the transition automatically. */
function flashAccept(row: HTMLElement | null): void {
    if (!row) return;
    row.classList.add('ce-accept-flash');
    window.setTimeout(() => row.classList.remove('ce-accept-flash'), 120);
}

/** The ⌘S save-shimmer (§C.3): EVERY captured rail in view recolours blue→green staggered
 *  top-to-bottom — the doc margin shimmers green on one keystroke. Fired from `onCommit`, so
 *  it covers BOTH commit paths (⌘S inside the editor and the toolbar button). Gated in
 *  motion.ts (reduced motion → no shimmer; the rails still graduate to pending on reconcile). */
function fireSaveShimmer(): void {
    saveShimmer([...document.querySelectorAll<HTMLElement>('.ce-captured-rail')]);
}

/** Toolbar Commit & send (§C.3): the plane launches "sent" (the shimmer rides `onCommit`).
 *  Routes through the editor's commit so the latest keystroke is flushed first. */
function triggerCommit(plane?: SVGElement | null): void {
    if (plane) launchPlane(plane);
    wholeEditor?.commit();
}

// ── Cross-surface bridge handlers (P2 / §A) ──────────────────────────────────
/** A keystroke inside feature `fid` (§A.1 doc→code). Debounce 180 ms, then ask the host to
 *  open that feature's bound code Beside + light the implicated lines. Only features with ≥1
 *  binding OR an unrealized placeholder are bridged; a realized-but-bound-less feature has no
 *  code to show (the host's no-binding path A.4 covers the unrealized case). */
function onBridgeEdit(fid: string | null): void {
    if (!fid) { bridgeDebounce.clear(); return; }
    const node = payload.nodes[fid];
    if (!node) { bridgeDebounce.clear(); return; }
    // Eager: a feature with a binding, or an unrealized plan placeholder (A.4 file-level lens).
    const bridgeable = (node.bindings && node.bindings.length > 0) || node.realized === false;
    if (!bridgeable) { bridgeDebounce.clear(); return; }
    bridgeDebounce.fire(() => {
        bridgeFid = fid;
        vscode.postMessage({ kind: 'bridge-open', fid });
    });
}

/** The caret left the bridged feature (§A.1): cancel a pending open and clear the code-side
 *  highlight — the pane STAYS open (opening is eager, closing is the user's call). */
function onBridgeCaretLeave(): void {
    bridgeDebounce.clear();
    if (bridgeFid === null) return;
    const prev = bridgeFid;
    bridgeFid = null;
    vscode.postMessage({ kind: 'bridge-dim', fid: prev });
}

/** Code→doc (§A.3): a bound source file was edited — spark the touched headings in the doc
 *  pane and briefly pulse their tree rows (so the navigator shows where the action is even
 *  when that section is scrolled off). The persistent blue underline tick settles after the
 *  spark holds; a payload reconcile clears it. */
const treePulseTimers = new Map<string, number>();
function onCodeTouch(fids: string[], big: string[]): void {
    if (!fids.length) return;
    wholeEditor?.touchFeatures(fids, new Set(big));
    for (const fid of fids) {
        const row = document.querySelector<HTMLElement>('.row[data-id="' + cssEsc(fid) + '"]');
        if (!row) continue;
        // a transient blue active-write pulse on the row's badge (§A.3) — add a momentary
        // badge if the row has none, then drop it after the 1.4s pulse so the resting row
        // is unchanged.
        let badge = row.querySelector<HTMLElement>('.badge.active-write.ce-touch-pulse');
        if (!badge) {
            badge = el('span', 'badge active-write ce-touch-pulse');
            row.append(badge);
        }
        const prev = treePulseTimers.get(fid);
        if (prev) clearTimeout(prev);
        treePulseTimers.set(fid, window.setTimeout(() => {
            treePulseTimers.delete(fid);
            document.querySelector('.row[data-id="' + cssEsc(fid) + '"] .ce-touch-pulse')?.remove();
        }, 1400));
    }
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

// ─── Tree auto-center (U2) ───────────────────────────────────────────────────
/** Ease the active feature's row to the vertical centre of the tree pane — scroll-driven only.
 *  No-op while a manual-scroll suppress window is open or the row is already centred (deadband).
 *  Cancels any in-flight tween first so rapid section changes don't queue. */
function centerTreeRow(fid: string): void {
    if (suppressAutoCenter) return;
    const tree = document.querySelector('.tree') as HTMLElement | null;
    if (!tree) return;
    const row = tree.querySelector<HTMLElement>('.row[data-id="' + cssEsc(fid) + '"]');
    if (!row) return;
    const target = centerScrollTarget(
        row.offsetTop, row.offsetHeight, tree.clientHeight, tree.scrollHeight, tree.scrollTop,
    );
    if (target === tree.scrollTop) return; // within the centre deadband — nothing to do
    if (centerTween) centerTween.cancel();
    treeTweenActive = true;
    centerTween = tweenScrollTop(tree, target, {
        duration: 200, ease: 'outQuad',
        onComplete: () => { treeTweenActive = false; centerTween = null; },
    });
}

/** Begin a manual-scroll suppression: cancel any auto-center tween (the user takes over) and
 *  open an ~800ms window where the scroll-spy won't yank the pane back. */
function beginManualScroll(): void {
    cancelCenter();
    suppressAutoCenter = true;
    if (suppressTimer) clearTimeout(suppressTimer);
    suppressTimer = window.setTimeout(() => { suppressAutoCenter = false; }, 800);
    persistUiState(); // capture the new tree scroll position
}
/** From the tree's `scroll` event — could be the user OR our own tween / reconcile restore, so
 *  ignore those so we don't suppress ourselves. Catches scrollbar-drag (which fires no wheel). */
function onManualTreeScroll(): void {
    if (treeTweenActive || programmaticTreeScroll) return;
    beginManualScroll();
}
/** From the tree's `wheel` event — ALWAYS a user action (our tween writes scrollTop, never
 *  wheels), so it cancels an in-flight center tween immediately rather than waiting it out. */
function onTreeWheel(): void {
    beginManualScroll();
}

/** Stop any in-flight auto-center tween (before a tree re-render, or when the user takes over). */
function cancelCenter(): void {
    if (centerTween) { centerTween.cancel(); centerTween = null; }
    treeTweenActive = false;
}

/** Restore a tree scrollTop programmatically WITHOUT tripping the manual-scroll suppression. */
function setTreeScroll(treeEl: HTMLElement, top: number): void {
    programmaticTreeScroll = true;
    treeEl.scrollTop = top;
    requestAnimationFrame(() => { programmaticTreeScroll = false; });
}

// ─── UI-state persistence (U5) ───────────────────────────────────────────────
/** Snapshot the current selection/expansion/caret/scroll into vscode.setState (debounced). */
function persistUiState(): void {
    if (persistTimer) clearTimeout(persistTimer);
    persistTimer = window.setTimeout(() => {
        persistTimer = 0;
        const tree = document.querySelector('.tree') as HTMLElement | null;
        vscode.setState(serializeUiState({
            selectedId,
            expanded: [...expanded],
            caretPos: wholeEditor ? wholeEditor.getCaretPos() : 0,
            treeScroll: tree ? tree.scrollTop : 0,
            docScroll: wholeEditor ? wholeEditor.getScrollTop() : 0,
            treeWidth,
            focusMode,
        }));
    }, 400);
}

/** Apply the scroll + caret half of a restore after the first render: tree scroll now, and the
 *  editor caret + doc scroll on the next frame so the editor's own first-setDoc placement
 *  (heading fallback) has already run and doesn't clobber the restored caret (KTD3). */
function applyPendingRestore(): void {
    if (!pendingRestore) return;
    const r = pendingRestore;
    pendingRestore = null;
    const tree = document.querySelector('.tree') as HTMLElement | null;
    if (tree) setTreeScroll(tree, r.treeScroll);
    const ed = wholeEditor; // capture identity — a re-mount before the rAF must not get a stale restore
    requestAnimationFrame(() => {
        if (!wholeEditor || wholeEditor !== ed) return;
        if (r.caretPos > 0) wholeEditor.setCaretPos(r.caretPos);
        wholeEditor.setScrollTop(r.docScroll);
    });
}

// ─── Top-level render ─────────────────────────────────────────────────────────
function renderAll(): void {
    app.replaceChildren();
    app.append(renderToolbar());
    const main = el('div', 'main');
    main.append(renderTree(), renderColResizer(), renderDocHost());
    app.append(main);
    applyTreeWidth();
    if (!didFocusTree) { didFocusTree = true; queueMicrotask(focusTree); }
}

// ─── Continuous pane resize ───────────────────────────────────────────────────
const TREE_MIN = 200, TREE_MAX = 560, TREE_DEFAULT = 300;

/** Push the current tree width onto the layout as a CSS var (clamped). A `0` width means
 *  "use the stylesheet default" — so a fresh editor isn't pinned until the user drags. */
function applyTreeWidth(): void {
    const main = document.querySelector<HTMLElement>('.main');
    if (!main) return;
    if (treeWidth > 0) main.style.setProperty('--tree-w', `${Math.round(clampTreeWidth(treeWidth))}px`);
    else main.style.removeProperty('--tree-w');
}
function clampTreeWidth(px: number): number {
    return Math.max(TREE_MIN, Math.min(TREE_MAX, px));
}

/** The draggable gutter between the nav tree and the doc. Pointer-capture drag updates the
 *  tree width live (clamped); double-click resets to the default. Calm: the divider is a
 *  hairline that brightens on hover/drag, never a heavy handle. */
function renderColResizer(): HTMLElement {
    const r = el('div', 'col-resizer');
    r.setAttribute('role', 'separator');
    r.setAttribute('aria-orientation', 'vertical');
    r.title = 'Drag to resize · double-click to reset';
    r.addEventListener('pointerdown', ev => {
        ev.preventDefault();
        const main = document.querySelector<HTMLElement>('.main');
        const tree = document.querySelector<HTMLElement>('.tree');
        if (!main || !tree) return;
        const startX = ev.clientX;
        const startW = tree.getBoundingClientRect().width;
        r.setPointerCapture(ev.pointerId);
        document.body.classList.add('col-resizing');
        const onMove = (e: PointerEvent): void => {
            treeWidth = clampTreeWidth(startW + (e.clientX - startX));
            main.style.setProperty('--tree-w', `${Math.round(treeWidth)}px`);
        };
        const onUp = (e: PointerEvent): void => {
            r.releasePointerCapture(ev.pointerId);
            r.removeEventListener('pointermove', onMove);
            r.removeEventListener('pointerup', onUp);
            document.body.classList.remove('col-resizing');
            void e;
            persistUiState();
        };
        r.addEventListener('pointermove', onMove);
        r.addEventListener('pointerup', onUp);
    });
    r.addEventListener('dblclick', () => {
        treeWidth = TREE_DEFAULT;
        applyTreeWidth();
        persistUiState();
    });
    return r;
}

// ─── Reconcile (subsequent payloads) ─────────────────────────────────────────
function reconcile(): void {
    document.querySelector('.toolbar')?.replaceWith(renderToolbar());
    reconcileTree();
    // Refresh the dependency spotlight: a loop pass may have changed payload.threads
    // while a feature stayed selected, so the dimmed/tinted set must be recomputed
    // (reconcileTree only re-applies the existing focusState, never recomputes it).
    if (selectedId && computeFocus(selectedId)) applyFocusClasses();
    // Feed the whole-doc editor the new settled doc (it ignores updates while the
    // user has unsettled local edits, so typing isn't clobbered) + the latest diffs.
    if (payload.doc && wholeEditor) {
        // setSuggestions BEFORE setDoc: setDoc's splice/strip read the suggestion set,
        // so the authoritative list must be current first — otherwise a withdrawn or
        // freshly-echoed suggestion is one payload stale and the live prose isn't
        // reconciled this turn (the WS4 withdraw-revert + echo-freshness fix).
        wholeEditor.setSuggestions(payload.suggestions ?? []);
        wholeEditor.setThreads(payload.threads ?? {});
        wholeEditor.setPhases(payload.sync.phase ?? {});
        wholeEditor.setSteps(payload.sync.steps ?? {});
        // Lifecycle split (U3/U4): drafts = recorded-but-not-sent → "captured"; held minus
        // drafts = handed-off (staged & sent) → "pending". Save/Commit (hand-off) moves a
        // feature from drafts → handed-off.
        wholeEditor.setHeld(handedOff(payload), payload.holdDetail ?? {});
        wholeEditor.setDrafts(payload.drafts ?? []);
        wholeEditor.setBlocks(payload.blocks ?? {});
        wholeEditor.setComments(payload.comments ?? []);
        wholeEditor.setHoverCards(payload.hoverCards ?? null);
        wholeEditor.setMintedMap(payload.mintedByLocalId ?? {});  // before setDoc — exact fid reconcile
        wholeEditor.setDoc(payload.doc);
        applyGlance();     // refresh pitch map (a loop pass may have rewritten pitches)
    } else {
        document.querySelector('.doc-host')?.replaceWith(renderDocHost());
    }
}

function reconcileTree(): void {
    const tree = document.querySelector('.tree') as HTMLElement | null;
    if (!tree) return;
    cancelCenter();                  // a tween into the about-to-be-replaced element would orphan
    const scroll = tree.scrollTop;
    const had = treeHasFocus();
    const next = renderTree();
    tree.replaceWith(next);
    setTreeScroll(next, scroll);     // restore without tripping the manual-scroll suppression
    if (had) next.focus({ preventScroll: true });
}

// ─── Toolbar ─────────────────────────────────────────────────────────────────
function rerenderToolbar(): void {
    document.querySelector('.toolbar')?.replaceWith(renderToolbar());
}

function renderToolbar(): HTMLElement {
    const t = el('div', 'toolbar');
    const p = el('div', 'path');
    p.append(el('span', 'dim', payload.rootName + ' / .codoc / '));
    p.append(el('span', 'file', 'tree.codoc'));
    t.append(p);

    const state = payload.status.state || 'in_sync';
    const pending = payload.status.pending || 0;
    const s = el('div', 'status ' + state);
    s.append(el('span', 'dot'));
    s.append(el('span', 'status-label', statusLabel(state, pending)));
    t.append(s);

    t.append(el('div', 'spacer'));

    // A single right-aligned action group keeps the bar calm: view toggles, then the
    // contextual review / hand-off actions only when there's something to act on.
    const actions = el('div', 'tb-actions');

    // Focus toggle: dim tree rows unrelated to the selection (its depends-on / used-by
    // neighbours stay lit). Off by default; replaces the old dependency-flow panel.
    const focus = el('button', 'toggle focus' + (focusMode ? ' active' : ''),
        (focusMode ? '◉ ' : '◎ ') + 'Focus');
    focus.title = focusMode
        ? 'Focus on — the tree dims everything outside the selected feature’s dependencies. Click to show all.'
        : 'Focus — dim the tree to just the selected feature’s dependencies (depends-on + used-by)';
    focus.setAttribute('aria-pressed', String(focusMode));
    focus.onclick = () => {
        focusMode = !focusMode;
        computeFocus(selectedId);
        applyFocusClasses();   // re-tag every row for the new on/off state
        rerenderToolbar();
        persistUiState();
    };
    actions.append(focus);

    // Glance toggle (B-U2): collapse every feature to its one-line pitch. Tree-wide,
    // default off, persisted per-workspace. A decoration only — the doc is untouched.
    const glance = el('button', 'toggle glance' + (prefs.glance ? ' active' : ''),
        (prefs.glance ? '◢ ' : '◿ ') + 'Glance');
    glance.title = prefs.glance
        ? 'Glance on — features show their one-line pitch. Click to expand full prose.'
        : 'Glance — collapse every feature to its one-line pitch';
    glance.setAttribute('aria-pressed', String(prefs.glance));
    glance.onclick = () => { setPref('glance', !prefs.glance); applyGlance(); rerenderToolbar(); };
    actions.append(glance);

    const ids = payload.pendingEventIds;
    if (ids.length) {
        actions.append(el('span', 'tb-sep'));
        const accAll = el('button', 'toggle bulk', `✓ Accept all (${ids.length})`);
        accAll.onclick = () => { beginApplying(null); postVerdict(ids.slice(), true); };
        const rejAll = el('button', 'toggle bulk', `✗ Reject all (${ids.length})`);
        rejAll.onclick = () => { beginApplying(null); postVerdict(ids.slice(), false); };
        actions.append(accAll, rejAll);
    }
    t.append(actions);

    // Commit & send (U4 — save = stage & send): the one gesture that hands the staged
    // (captured) code-implying edits to the agent. Shown only when the daemon is holding
    // such edits (prose commits live, so it raises nothing). Equivalent to ⌘S in the
    // editor; routes through the editor so the latest unsettled keystroke is flushed first.
    const drafts = payload.drafts ?? [];
    if (drafts.length) {
        actions.append(el('span', 'tb-sep'));
        const hand = el('button', 'toggle bulk handoff');
        // §C.1/§C.3: a paper-plane glyph = "sent". On click it launches (up-right) and the
        // captured rails shimmer blue→green — the two big save-moments chained into one.
        const plane = icon('paper-plane-tilt', { className: 'ce-handoff-plane' });
        hand.append(plane, document.createTextNode(` Commit & send (${drafts.length})`));
        hand.title = drafts.length === 1
            ? 'Commit & send (⌘S) — hand this staged edit to the agent to implement now.'
            : `Commit & send (⌘S) — hand all ${drafts.length} staged edits to the agent to implement now.`;
        hand.onclick = () => triggerCommit(plane);
        actions.append(hand);
    }

    // (the "⇄ text" toggle was removed — the webview is the single surface, D1; the
    //  raw-text editor is still reachable via "Reopen Editor With… → Text Editor".)
    return t;
}

// ─── Tree pane (navigation) ────────────────────────────────────────────────
function renderTree(): HTMLElement {
    const wrap = el('div', 'tree');
    wrap.tabIndex = 0;
    wrap.addEventListener('scroll', onManualTreeScroll, { passive: true });
    wrap.addEventListener('wheel', onTreeWheel, { passive: true });
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
    markFocusRow(row, id); // dependency spotlight (WS5) — survives reconciles
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
    // Edit lifecycle (U3/U4): a held DRAFT is "captured" — recorded & staged locally but
    // NOT sent (Save / Commit hands it off). A handed-off edit is "pending" — sent, the
    // agent will implement it. The active shimmer (write/read) is a separate axis.
    if (draftSet.has(id)) {
        const b = el('span', 'badge captured');
        b.title = 'Captured — recorded & staged locally. Save (⌘S) or Commit to send it to the agent.';
        b.append(icon('circle-dashed')); // §C.1: thin dashed circle = mine, local
        row.append(b);
    } else if (awaitingAI.has(id)) {
        const b = el('span', 'badge pending');
        b.title = 'Pending — staged & sent; the agent will implement it (run /codoc:sync if no daemon).';
        // §C.1 "open/fill = phase": captured (draft) draws the hollow circle; once handed off
        // (sent & queued) the badge advances to the FILLED diamond — "◆ queued".
        b.append(icon('diamond-fill'));
        row.append(b);
    }
    // "review what the AI did": a realization changed this feature beyond the one you
    // edited (U5 scope divergence). The change itself shows as a proposal below.
    if (divergent[id]) {
        const b = el('span', 'badge divergent');
        b.title = 'Review — the AI changed this while realizing another of your edits.';
        b.append(icon('warning-diamond')); // §C.1: warning-diamond = divergent
        row.append(b);
    }
    if (!n.realized) {
        const b = el('span', 'badge unrealized');
        b.append(icon('circle-dashed')); // §C.1: thin dashed circle = accepted plan, no code yet
        row.append(b);
    }
    if (n.proposal?.op === 'amend') row.append(el('span', 'badge amend'));
    if (n.proposal?.op === 'retire') row.append(el('span', 'badge retire'));

    // (code refs moved into the document's inline "threads" line under each heading — U4)

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
    computeFocus(null); // editor torn down → clear the dependency-spotlight body class
    if (!payload.doc) {
        host.append(el('div', 'doc empty', 'No features yet. Run `codoc init` to bootstrap the tree.'));
        return host;
    }
    wholeEditor = mountWholeDocEditor(host, {
        controller: authorController,
        getSymbols: () => payload.symbols ?? [],
        onSettle: doc => vscode.postMessage({ kind: 'doc-settle', doc }),
        onCommit: doc => { fireSaveShimmer(); vscode.postMessage({ kind: 'commit', doc }); },
        onAccept: s => { if (s.eventId) { beginApplying(null); postVerdict([s.eventId], true); } },
        onReject: s => { if (s.eventId) { beginApplying(null); postVerdict([s.eventId], false); } },
        onWithdrawRealization: featureId => vscode.postMessage({ kind: 'withdraw-realization', featureId }),
        onOpenBinding: (file, symbol) => vscode.postMessage({ kind: 'open-binding', file, symbol }),
        onConsult: url => vscode.postMessage({ kind: 'open-link', url }),
        onCommentCreate: (doc, thread, media) => vscode.postMessage({ kind: 'comment-create', doc, thread, mediaData: media?.data, mediaMime: media?.mime }),
        onCommentEdit: (id, body) => vscode.postMessage({ kind: 'comment-edit', id, body }),
        onCommentResolve: (doc, id) => vscode.postMessage({ kind: 'comment-resolve', doc, id }),
        onActiveFeature: (fid, source) => {
            if (!fid) { onBridgeCaretLeave(); return; }
            // Caret moved to a different feature without an intervening edit → clear the
            // code-side bridge highlight (the pane stays open, §A.1). An edit re-opens it.
            if (fid !== bridgeFid) onBridgeCaretLeave();
            syncingFromEditor = true;
            setSelected(fid, false); // highlight the tree row, don't re-scroll the editor
            syncingFromEditor = false;
            // Eased re-center ONLY on the scroll-driven spy — a caret move (source==='selection')
            // just highlights, else typing would animate the tree on every keystroke (KTD2).
            if (shouldCenter(source)) centerTreeRow(fid);
        },
        onEditFeature: fid => onBridgeEdit(fid),  // P2 doc→code (§A.1), debounced below
        onHoverFeature: fid => peekTreeRow(fid), // WS5: preview a dependency link's target
        onBlockEdit: edit => vscode.postMessage({ kind: 'block-edit', block: edit }),  // v6
    });
    wholeEditor.setSuggestions(payload.suggestions ?? []); // before setDoc — see reconcile()
    wholeEditor.setThreads(payload.threads ?? {});
    wholeEditor.setPhases(payload.sync.phase ?? {});
    wholeEditor.setSteps(payload.sync.steps ?? {});
    wholeEditor.setHeld(handedOff(payload), payload.holdDetail ?? {});
    wholeEditor.setDrafts(payload.drafts ?? []);
    wholeEditor.setBlocks(payload.blocks ?? {});
    wholeEditor.setComments(payload.comments ?? []);
    wholeEditor.setHoverCards(payload.hoverCards ?? null);
    wholeEditor.setMintedMap(payload.mintedByLocalId ?? {});  // before setDoc — exact fid reconcile
    wholeEditor.setDoc(payload.doc);
    applyGlance();      // seed pitch + glance state into the fresh editor
    // Presence rides the doc surface — re-place the avatar as the surface scrolls (the agent's
    // heading moves under a static avatar). The surface is recreated here, so re-bind.
    host.querySelector<HTMLElement>('.ce-whole-surface')
        ?.addEventListener('scroll', () => presence.reposition(), { passive: true });
    return host;
}

// ─── Focus dimming (WS5) ──────────────────────────────────────────────────────
/** Recompute which tree rows the focused feature depends on / is used by, and toggle
 *  the body dimming class. No dependencies → no dimming (spotlighting an isolated node
 *  by greying the whole tree would be noise, not signal). Returns whether the spotlight
 *  actually changed, so callers can skip the O(rows) re-tag on an unchanged focus
 *  (arrow-key nav usually stays within the same dependency cluster). */
function computeFocus(fid: string | null): boolean {
    const prev = focusState;
    // No toggle, no focus, or a feature with no surfaced dependencies → clear (dimming an
    // isolated node by greying the whole tree would be noise, not signal).
    const threads = fid && focusMode ? payload.threads?.[fid] : undefined;
    const reads = threads?.reads ?? [];
    const usedBy = threads?.usedBy ?? [];
    if (!fid || !focusMode || (!reads.length && !usedBy.length)) {
        focusState = null;
        document.body.classList.remove('focus-dimming');
        return prev !== null;
    }
    // The lit set: the focus, its reads + used-by neighbours, and the ancestor chain of all
    // of them (so a lit row never floats under a greyed-out parent). Everything else dims.
    const related = new Set<string>([fid, ...reads.map(r => r.toId), ...usedBy.map(r => r.toId)]);
    for (const id of [...related]) {
        let n: UINode | undefined = payload.nodes[id];
        while (n && n.parent_id) { related.add(n.parent_id); n = payload.nodes[n.parent_id]; }
    }
    focusState = { fid, related };
    document.body.classList.add('focus-dimming');
    // Cheap change check so callers skip the O(rows) re-tag when the lit set is unchanged
    // (arrow-nav / scroll often stays within one dependency cluster).
    const same = prev && prev.fid === fid && prev.related.size === related.size
        && [...related].every(x => prev.related.has(x));
    return !same;
}

/** Tag a row with its dependency relationship to the focused feature (called from
 *  appendRow so it survives reconciles, and applied in bulk by applyFocusClasses). */
function markFocusRow(row: HTMLElement, id: string): void {
    row.classList.remove('dep-related');
    if (!focusState) return;
    if (focusState.related.has(id)) row.classList.add('dep-related'); // lit; everything else dims
}

/** Re-tag every existing tree row (cheap, no re-render) after the focus changes. */
function applyFocusClasses(): void {
    document.querySelectorAll<HTMLElement>('.tree .row[data-id]').forEach(row => markFocusRow(row, row.dataset.id!));
}

/** Transient preview of a depends-on / used-by link target: highlight + scroll its
 *  tree row into view without moving the selection (WS5 hover-navigate). The highlight
 *  is instant feedback; the scroll is debounced so flicking across several links doesn't
 *  yank the tree pane out from under a manual scroll. The previously-peeked row is
 *  tracked so clearing is O(1), not an all-rows query. */
let peekedRow: HTMLElement | null = null;
let peekTimer = 0;
function peekTreeRow(fid: string | null): void {
    if (peekTimer) { clearTimeout(peekTimer); peekTimer = 0; }
    if (peekedRow) { peekedRow.classList.remove('hover-peek'); peekedRow = null; }
    if (!fid) return;
    const row = document.querySelector<HTMLElement>('.row[data-id="' + cssEsc(fid) + '"]');
    if (!row) return;
    row.classList.add('hover-peek');
    peekedRow = row;
    peekTimer = window.setTimeout(() => { peekTimer = 0; row.scrollIntoView({ block: 'nearest' }); }, 90);
}

// ─── Selection (tree ↔ editor) ───────────────────────────────────────────────
function setSelected(id: string | null, scrollDoc: boolean): void {
    selectedId = id;
    noteRecent(id);   // P4 §D.3: feed the ⌘K "Recent features" welcome list
    // Reveal a selected node's ancestors so its tree row exists and can be marked
    // .selected (cheap — only re-renders when an ancestor was actually collapsed).
    if (id) revealAncestors(id);
    // Recompute the dependency spotlight; only re-tag every row when it actually
    // changed (a re-render via revealAncestors re-applies the unchanged state in
    // appendRow, so skipping the bulk pass here is safe).
    if (computeFocus(id)) applyFocusClasses();
    document.querySelectorAll('.row.selected').forEach(r => r.classList.remove('selected'));
    if (!id) return;
    const rowEl = document.querySelector<HTMLElement>('.row[data-id="' + cssEsc(id) + '"]');
    if (rowEl) {
        rowEl.classList.add('selected');
        // Only scroll the tree when the selection came from the tree itself (click /
        // keyboard nav) — that path SNAPS and cancels any eased auto-center so the two don't
        // fight. An editor caret move (syncingFromEditor) just highlights the row; the eased
        // re-center is driven separately by the scroll-spy via centerTreeRow.
        if (!syncingFromEditor) { cancelCenter(); rowEl.scrollIntoView({ block: 'nearest' }); }
    }
    // Scroll the editor to this feature — unless the selection came from the editor's
    // own caret (avoid fighting it) or the id is a pending ghost (no live heading).
    if (scrollDoc && !syncingFromEditor && wholeEditor && id.startsWith('f-')) {
        wholeEditor.scrollToFeature(id);
    }
    persistUiState();
}

/** Expand every ancestor of `id` so its tree row becomes visible. */
function revealAncestors(id: string): void {
    let changed = false;
    let n: UINode | undefined = payload.nodes[id];
    while (n && n.parent_id) {
        if (!expanded.has(n.parent_id)) { expanded.add(n.parent_id); changed = true; }
        n = payload.nodes[n.parent_id];
    }
    if (changed) rerenderTree();
}

function toggle(id: string): void {
    if (expanded.has(id)) expanded.delete(id); else expanded.add(id);
    rerenderTree(true);
    persistUiState();
}

/** Re-render the tree pane in place (keeping the optimistic applying state); optionally re-focus. */
function rerenderTree(focus = false): void {
    const tree = document.querySelector('.tree');
    if (!tree) return;
    cancelCenter();
    const next = renderTree();
    tree.replaceWith(next);
    reapplyApplyingTo(next);
    if (focus) (next as HTMLElement).focus({ preventScroll: true });
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

// ⌘K command palette (P4 / §D.4): a CAPTURE-phase listener on document so the editor keymap
// can't swallow it, and so the palette's own ↑/↓/↵/Esc are handled before anything else while
// it's open. ⌘K toggles; the input owns plain typing. The accelerator is ⌘ on macOS and Ctrl
// elsewhere — gating Ctrl off mac avoids colliding with the native Ctrl+K (delete-to-EOL) inside
// text inputs.
const IS_MAC = typeof navigator !== 'undefined'
    && /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent || '');
document.addEventListener('keydown', ev => {
    if (palette.isOpen && palette.onKeydown(ev)) return;
    if ((IS_MAC ? ev.metaKey : ev.ctrlKey) && (ev.key === 'k' || ev.key === 'K')) {
        ev.preventDefault();
        ev.stopPropagation();
        palette.toggle();
    }
}, true);

document.addEventListener('keydown', ev => {
    if (palette.isOpen) return;                        // the palette owns keys while open
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
    const msg = ev.data as { kind: string; payload: DocPayload; fids?: string[]; big?: string[] };
    // Code→doc spark (P2 / §A.3): a bound source file was edited — land the inbound glyph on
    // each touched heading and pulse its tree row, even when that section is scrolled off.
    if (msg.kind === 'code-touch') { onCodeTouch(msg.fids ?? [], msg.big ?? []); return; }
    if (msg.kind !== 'doc') return;
    if (msg.payload.rev < lastRev) return; // ignore stale posts
    lastRev = msg.payload.rev;
    payload = msg.payload;
    awaitingAI = new Set(payload.awaitingAI ?? []);
    draftSet = new Set(payload.drafts ?? []);
    divergent = payload.divergent ?? {};
    // endApplying MUST stay after the stale-rev guard — a stale (dropped) post must
    // not clear the optimistic applying state for a verdict still in flight.
    endApplying();

    // Seed per-workspace prefs from the host (workspaceState) once — afterward the
    // local copy is authoritative (optimistic toggles), so a repost can't revert a
    // pref the user just changed.
    if (!prefsSeeded && payload.prefs) { prefs = payload.prefs; prefsSeeded = true; }

    if (selectedId && !payload.nodes[selectedId]) selectedId = null;
    for (const id of [...expanded]) if (!payload.nodes[id]) expanded.delete(id);
    if (firstPayload) {
        firstPayload = false;
        // Restore persisted UI state (U5) if present — validated against the live payload — else
        // fall back to expand-all. Seeding here (before renderAll) means the FIRST paint uses the
        // restored expansion/selection, with no expand-all flash.
        const restored = deserializeUiState(vscode.getState());
        if (restored) {
            for (const id of restored.expanded) if (payload.nodes[id]) expanded.add(id);
            if (restored.selectedId && payload.nodes[restored.selectedId]) selectedId = restored.selectedId;
            treeWidth = restored.treeWidth;
            focusMode = restored.focusMode;
            pendingRestore = restored; // scroll + caret applied after the editor mounts
        } else {
            // Expand every parent so the whole tree is visible by default.
            for (const id of Object.keys(payload.nodes)) {
                if (payload.nodes[id].children.length) expanded.add(id);
            }
        }
        if (selectedId == null) selectedId = payload.roots[0] ?? null;
    }

    if (!mounted) { mounted = true; renderAll(); applyPendingRestore(); } else { reconcile(); }
    // Presence rides every payload: a new sync.phase / realize moves the avatar (§B). Deferred
    // a frame so the freshly-rendered headings/rows exist for the anchor query.
    requestAnimationFrame(updatePresence);
});

// Re-place the (static) avatar when its anchor heading/row moves under it.
window.addEventListener('resize', () => presence.reposition(), { passive: true });

/** Single-source the resolving-phase mask glyphs (§C.4): the CSS pseudo-elements read
 *  `--phase-glyph-{editing,reflecting}`, which we set from the icon registry so the heading
 *  glyph can never drift from `icon('pen-nib'/'arrows-clockwise')`. */
function setPhaseGlyphVars(): void {
    const root = document.documentElement.style;
    root.setProperty('--phase-glyph-editing', iconMaskDataUri('pen-nib'));
    root.setProperty('--phase-glyph-reflecting', iconMaskDataUri('arrows-clockwise'));
}
setPhaseGlyphVars();

vscode.postMessage({ kind: 'ready' });
