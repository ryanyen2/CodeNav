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
import { isSaveChord } from './save-chord';
import { AuthorController } from './tiptap/author-plugin';
import {
    kindGlyph, consequenceOf, consequenceVerb, consequenceNote, leavesForAgent,
    type Consequence,
} from '../state/grammar';
import { featureState, stateBadge, PLANNED_TITLE } from '../state/feature-state';
import { unseenEdits, catchUpLabel, keepAllLabel, keepAllVerdicts } from '../state/auto-edits';
import { shouldQuietSkeleton } from '../state/translate-model';
import { icon, iconMaskDataUri } from './icons';
import { tweenScrollTop, TweenController, popLanded, spinReject, saveShimmer, launchPlane } from './motion';
import { shouldCenter, centerScrollTarget } from './tree-center';
import { BridgeDebounce } from '../state/bridge';
import { deriveAgentPresences, type PresencePhase } from '../state/presence';
import { PresenceLayer } from './presence-layer';
import { CommandPalette } from './palette-view';
import { createAskBar, type AskBarHandle } from './ask-bar';
import { createFindView, type FindViewHandle } from './find-view';
import type { PaletteContext, PaletteItem } from './palette';
import { serializeUiState, deserializeUiState, UiState } from './ui-state';
import type { DocPayload, UINode, WebviewMessage, WebviewPrefs } from './protocol';
import { acquireHostApi, isVsCodeHost, type Delivery } from './host-bridge';
import { mountViewerStatus } from './viewer-status';
import { langAttrFor, languageName, shortLanguageLabel, pendingForTarget } from './doc-lang';
import type { BusyInfo } from './tiptap/busy-decorations';
import { createCommandEmitter, commandMessage, type CommandEmitter } from './command-emitter';
import { moveCommand } from '../state/commands-from-doc';
import type { CommandEntry } from '../state/edits-channel';

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
// The /codoc:ask header and the ⌘F widget. Both live inside .doc-host and are
// rebuilt with it; `reconcile` keeps the editor alive, so a search or a
// walkthrough survives every ordinary payload repaint.
let askBar: AskBarHandle | null = null;
let findView: FindViewHandle | null = null;
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
// Sections being rewritten under the reader RIGHT NOW — translation batches pending
// + the agent applying (reflecting). Drives the skeleton shimmer in BOTH panes and
// the per-section edit guard in the editor. Recomputed per payload (busyFromPayload).
let busyByFid: Record<string, BusyInfo> = {};
// Two-stage language switch (stage 2): the pending "Translate N nodes?" offer. Held
// module-level because the toolbar re-renders on every payload — the offer must
// survive the repost that stage 1 (set-doc-language) itself triggers. Cleared on
// action, dismissal, or the language moving under it.
let translateOffer: { code: string; name: string; count: number } | null = null;
// Clicked "Translate now" but the CLI's first progress write hasn't landed yet —
// the button shows a spinner instead of pretending nothing is happening.
let translateStarting = false;
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
    const presences = deriveAgentPresences(phase, payload.sync.activeRead ?? [], payload.sync.agent ?? 'claude');
    presence.update(presences, payload.sync.realize);
    wholeEditor?.setRole(presences[0]?.role ?? 'claude');
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
                if (shift) { bridgeFid = item.arg; vscode.postMessage({ kind: 'bridge-open', fid: item.arg, reveal: true }); }
                setSelected(item.arg, true);
            }
            return;
        case 'open-code':
            if (item.arg) { bridgeFid = item.arg; vscode.postMessage({ kind: 'bridge-open', fid: item.arg, reveal: true }); }
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

// Hub only. In VS Code the answer to "what happens to what I type" is always the
// same, and a chip restating it would be noise; on the hub it depends on your
// GitHub permission and on whether the hub is reachable, and until now nothing
// said so. Created before the first payload so the very first delivery change —
// which can precede any projection — has somewhere to land.
const viewerStatus = isVsCodeHost()
    ? undefined
    : mountViewerStatus(document.body, showTransientNotice);

// Hub only. An authored edit reaches Loop B as an identity-keyed COMMAND (U3/U4); in VS
// Code the extension host derives those from a `doc-settle` because it owns the
// projection baselines, and on the hub the browser is the only party that ever sees a
// projection — so here the client emits them itself, through the same modules the host
// uses (see ./command-emitter). Before this, a remote settle posted the whole doc and the
// hub wrote it to `tree.doc.json`: a daemon-owned artifact nobody reads as input, so the
// edit was dropped and the derived re-render stalled.
const commands: CommandEmitter | undefined = isVsCodeHost() ? undefined : createCommandEmitter();

/** Put authored commands on the wire (hub only — in VS Code the host derives them). */
function postCommands(cmds: readonly CommandEntry[]): void {
    for (const c of cmds) vscode.postMessage(commandMessage(c));
}

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

function postVerdict(
    eventIds: string[], accept: boolean,
    edits?: { title?: string; description?: string },
): void {
    vscode.postMessage({ kind: 'verdict', eventIds, accept, ...(edits ? { edits } : {}) });
}

/**
 * The busy set for this payload: which sections are being REWRITTEN right now.
 *
 *   • translating — a `codoc translate` run lists the fid as pending; its prose is
 *     replaced batch by batch, and the skeleton clears per node as batches land.
 *   • applying — the agent is reflecting work back into this feature's entry
 *     (activity phase `reflecting`): its description is the write target of an
 *     in-flight store AMEND.
 *
 * `editing` (the agent in the feature's CODE) is deliberately NOT busy: the doc
 * text is not being rewritten then, and locking prose because code moves would
 * teach people the lock means nothing.
 */
// One notice per finished run (keyed by target + counts) — a payload repost must
// not re-toast the same summary.
let lastSkipNoticeKey = '';
function maybeNoticeSkips(tr: DocPayload['translation']): void {
    if (!tr || tr.running || !tr.skipped.length) return;
    const key = `${tr.target}:${tr.translated}:${tr.skipped.length}`;
    if (key === lastSkipNoticeKey) return;
    lastSkipNoticeKey = key;
    const first = tr.skipped[0];
    showTransientNotice(
        `Translation done — ${tr.translated} translated, ${tr.skipped.length} left as-is `
        + `(e.g. “${first.title || first.feature_id}”: ${first.reason}). `
        + 'Re-running retries them; details are in the codoc output.');
}

function busyFromPayload(p: DocPayload): Record<string, BusyInfo> {
    const out: Record<string, BusyInfo> = {};
    const tr = p.translation;
    if (tr?.running) {
        // A whole-document run is not the case the shimmer was drawn for. See
        // BusyInfo.quiet: translating a tree into a language it has never been in
        // makes every node pending at once, and dimming and sweeping all of them
        // costs the reader the document while telling them what the toolbar's
        // "translating 6/25" already says. Guard them all; animate none.
        const quiet = shouldQuietSkeleton(tr);
        for (const fid of tr.pending) {
            out[fid] = {
                kind: 'translating',
                quiet,
                label: `Translating into ${tr.targetName} (${tr.translated}/${tr.total} done). `
                    + 'This section updates itself when its batch lands — editing resumes then.',
            };
        }
    }
    for (const [fid, phase] of Object.entries(p.sync.phase ?? {})) {
        if (phase === 'reflecting' && !out[fid]) {
            out[fid] = {
                kind: 'applying',
                label: 'The agent is updating this entry right now — it lands in a moment. '
                    + 'Editing resumes when it does.',
            };
        }
    }
    return out;
}

// ── webview prefs (B-U2: overview dismiss + glance; W2: blame) ────────────────
function setPref(pref: 'glance' | 'blame', value: boolean): void {
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

/** W2: push the History (blame) stance into the editor (decoration only). */
function applyBlame(): void {
    document.body.classList.toggle('blame', !!prefs.blame);
    wholeEditor?.setBlame(!!prefs.blame);
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
    // W3: the timeout is not a silent revert any more — if no projection acked
    // the verdict (the ack = the payload whose arrival calls endApplying), say
    // so instead of letting the buttons quietly pop back with no explanation.
    //
    // The message has to distinguish two states it used to conflate. If a payload
    // came back marking the proposal `verdictPending`, the daemon DID read the click
    // and is holding it — a code-implying accept waits for a pass that can hand work
    // to the agent. Telling that user to check whether `codoc watch` is running sends
    // them to debug something that is not broken.
    applyingTimer = window.setTimeout(() => {
        const recorded = Object.values(payload.nodes)
            .some(n => n.proposal?.verdictPending);
        endApplying();
        showTransientNotice(recorded
            ? 'Recorded — this one waits for a pass that can hand code work to the agent '
              + '(a live Claude session, or run `codoc sync`).'
            : 'Verdict not picked up — is `codoc watch` (or a Claude session) running?');
    }, 5000);
}

/** A small self-dismissing notice pinned bottom-center — the honest channel for
 *  "your click did not take effect" (W3). One at a time; newest wins. */
function showTransientNotice(text: string): void {
    document.querySelector('.ce-transient-notice')?.remove();
    const n = el('div', 'ce-transient-notice', text);
    document.body.append(n);
    window.setTimeout(() => { n.classList.add('leaving'); window.setTimeout(() => n.remove(), 300); }, 6000);
}
function endApplying(): void {
    if (applyingTimer) { clearTimeout(applyingTimer); applyingTimer = 0; }
    document.body.classList.remove('applying');
    document.querySelectorAll('.applying').forEach(e => e.classList.remove('applying'));
}

/** After a tree re-render, re-disable verdict controls if a verdict is still
 *  in-flight (body.applying) — else freshly-built buttons become clickable again
 *  mid-apply and a duplicate verdict can fire.
 *
 *  `.ce-verdict` (the doc pane's per-feature strip) is in the list because a verdict
 *  is ONE decision with several surfaces: clicking Accept in the tree, or Accept all
 *  in the toolbar, must not leave the doc's own Accept live for the same event. CSS
 *  (`body.applying`) blocks the pointer immediately; this covers controls rebuilt
 *  while the verdict is still in flight. */
function reapplyApplyingTo(root: ParentNode): void {
    if (!document.body.classList.contains('applying')) return;
    root.querySelectorAll('.verdict, .inline-verdict, .ce-verdict').forEach(g => {
        g.classList.add('applying');
        g.querySelectorAll('button').forEach(b => { (b as HTMLButtonElement).disabled = true; });
    });
}

/**
 * The tree row's verdict pair. The row is too narrow for the doc pane's verb, so the
 * consequence rides the other three channels instead — the GLYPH (a paper plane
 * replaces the check when accepting hands work to the agent), the HOVER TEXT (the
 * same sentence the doc shows), and the MOTION (launch vs settle). A reader who
 * learns "plane = my code changes" in one place has learned it everywhere.
 */
function verdictButtons(eventId: string, cq: Consequence = 'record'): HTMLElement {
    const wrap = el('span', 'verdict cq-' + cq);
    const sends = leavesForAgent(cq);
    const acc = el('button', 'v-accept' + (sends ? ' sends' : ''));
    acc.title = consequenceVerb(cq) + ' — ' + consequenceNote(cq);
    acc.setAttribute('aria-label', consequenceVerb(cq));
    // §C.1: filled check = landed; the plane = "this leaves for the agent".
    acc.append(icon(sends ? 'paper-plane-tilt' : 'check-circle'));
    acc.onclick = ev => {
        ev.stopPropagation();
        // §C.3 accept: an optimistic cue + a green row flash. The authoritative removal
        // arrives async (or the 5s applyingTimer reverts) — we never fake-collapse the
        // row here, the next payload drops it once the verdict drains. A code-writing
        // accept LAUNCHES instead of settling: the same motion Commit & send uses,
        // because the same thing just happened.
        const glyph = acc.querySelector<HTMLElement>('.ce-icon');
        if (sends) launchPlane(glyph); else popLanded(glyph);
        flashAccept(wrap.closest<HTMLElement>('.row'));
        beginApplying(wrap); postVerdict([eventId], true);
    };
    const rej = el('button', 'v-reject');
    rej.title = sends ? 'Reject — discard this request. Nothing is written.'
                      : 'Reject — the tree keeps its current wording.';
    rej.setAttribute('aria-label', 'Reject');
    rej.append(icon('x-circle'));     // §C.1: x-circle = dismissed
    rej.onclick = ev => {
        ev.stopPropagation();
        spinReject(rej.querySelector<HTMLElement>('.ce-icon')); // §C.3: quieter than accept
        beginApplying(wrap); postVerdict([eventId], false);
    };
    wrap.append(rej, acc);
    return wrap;
}

/** A row whose verdict is recorded but not yet drained: the pair is replaced by a
 *  quiet "waiting" mark, so the click never looks like it failed and can't be fired
 *  a second time. Mirrors the doc pane's `.ce-verdict.waiting`. */
function verdictWaiting(): HTMLElement {
    const wrap = el('span', 'verdict waiting');
    wrap.title = 'Recorded — waiting for the daemon to apply it.';
    wrap.append(icon('arrows-clockwise'));
    return wrap;
}

/** The consequence of accepting a tree-pane proposal, from the sidecar overlay. */
function nodeConsequence(p: UINode['proposal']): Consequence {
    return consequenceOf(p?.writesCode ?? null, p?.tag);
}

/** Every live feature id in document order — the order the catch-up walk follows, so
 *  "next" means the next one down the page rather than an arbitrary map order. */
function docOrderFids(): string[] {
    const out: string[] = [];
    const walk = (id: string): void => {
        const n = payload.nodes[id];
        if (!n) return;
        if (!n.isProposal) out.push(id);
        n.children.forEach(walk);
    };
    payload.roots.forEach(walk);
    return out;
}

/** The consequence of a pending event id, looked up through whichever node carries
 *  it — a live node (amend/retire) or a ghost (add/move). Lets the bulk actions say
 *  what the batch will do without a second payload field. */
function consequenceForEvent(eventId: string): Consequence {
    for (const n of Object.values(payload.nodes)) {
        if (n.proposal?.eventId === eventId) return nodeConsequence(n.proposal);
    }
    return 'record';
}

/** Per-origin counts for the bulk tooltip ("4 from agent plan, 2 from code drift").
 *  A plan the user asked for and drift proposals codoc's background pass raised
 *  look identical as rows, and Accept-all resolves both — the breakdown is how the
 *  user learns what the batch actually contains before committing it. */
function originBreakdown(ids: string[]): string {
    const counts = new Map<string, number>();
    for (const id of ids) {
        let tag = 'code drift';
        for (const n of Object.values(payload.nodes)) {
            if (n.proposal?.eventId === id) { tag = n.proposal.tag || tag; break; }
        }
        counts.set(tag, (counts.get(tag) ?? 0) + 1);
    }
    return [...counts.entries()].map(([t, n]) => `${n} from ${t}`).join(', ');
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
        // Typing NEVER opens a split. A pilot editing a description had the bound
        // file swing open beside them mid-sentence — the screen rearranging while
        // the caret is in prose reads as the tool grabbing the wheel. The
        // highlight still lands when the file is already visible; OPENING it is
        // reserved for the explicit gestures (the binding chips, ⇧↵, the palette),
        // which say "show me the code" in so many words.
        vscode.postMessage({ kind: 'bridge-open', fid, reveal: false });
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

/** W3: transient "✓ saved to tree" confirmation on a heading whose prose-only
 *  edit just committed live (no queue, no badge — this is its only ack). */
const savedFlashTimers = new Map<string, number>();
function onSavedFlash(fids: string[]): void {
    for (const fid of fids) {
        const heading = document.querySelector<HTMLElement>(
            '.codoc-feature-heading[data-fid="' + cssEsc(fid) + '"]');
        if (!heading) continue;
        heading.classList.add('ce-saved-flash');
        const prev = savedFlashTimers.get(fid);
        if (prev) clearTimeout(prev);
        savedFlashTimers.set(fid, window.setTimeout(() => {
            heading.classList.remove('ce-saved-flash');
            savedFlashTimers.delete(fid);
        }, 1800));
    }
}

function onCodeTouch(fids: string[], big: string[]): void {
    if (!fids.length) return;
    wholeEditor?.touchFeatures(fids, new Set(big));
    for (const fid of fids) {
        const row = document.querySelector<HTMLElement>('.row[data-id="' + cssEsc(fid) + '"]');
        if (!row) continue;
        // a transient "working" pulse on the row's badge (§A.3) — add a momentary
        // badge if the row has none, then drop it after the 1.4s pulse so the resting row
        // is unchanged.
        let badge = row.querySelector<HTMLElement>('.badge.working.write.ce-touch-pulse');
        if (!badge) {
            badge = el('span', 'badge working write ce-touch-pulse');
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
    applyTreeLang();
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
    // Before the toolbar: a payload can carry a language the author just switched to,
    // and `renderAll` runs only once — so without this, <html lang> kept the language
    // the workspace had when the editor opened and every :lang() rule stayed wrong for
    // the rest of the session.
    applyTreeLang();
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
        wholeEditor.setAutoEdits(payload.autoEdits ?? {});
        wholeEditor.setBusy(busyByFid);   // skeleton shimmer + per-section edit guard
        wholeEditor.setSessionLive(payload.sync.sessionLive ?? false);
        wholeEditor.setHistory(payload.history ?? {});   // W2 blame data (refresh each pass)
        // Lifecycle split (U3/U4): drafts = recorded-but-not-sent → "captured"; held minus
        // drafts = handed-off (staged & sent) → "pending". Save/Commit (hand-off) moves a
        // feature from drafts → handed-off.
        wholeEditor.setHeld(handedOff(payload), payload.holdDetail ?? {});
        wholeEditor.setDrafts(payload.drafts ?? []);
        wholeEditor.setBlocks(payload.blocks ?? {});
        wholeEditor.setComments(payload.comments ?? []);
        wholeEditor.setHoverCards(payload.hoverCards ?? null);
        wholeEditor.setMintedMap(payload.mintedByLocalId ?? {});  // before setDoc — exact fid reconcile
        wholeEditor.setDoc(payload.doc, payload.baselineId);
        applyAsk();        // an ask can land, change, or clear at any point in an edit
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

// ─── Authoring language ──────────────────────────────────────────────────────

/** The tree's language tag (payload → 'en' on a legacy payload, which is what
 *  every pre-v6 tree was). */
function treeLangTag(): string {
    return payload.docLanguage?.code || 'en';
}

/** Stamp a row with its own `lang` only when it differs from the tree's — `lang`
 *  inherits, so tagging the majority would be noise and would bury the exceptions
 *  this attribute exists to mark. */
function applyNodeLang(host: HTMLElement, n: UINode): void {
    const tag = langAttrFor(n.lang, treeLangTag());
    if (tag) host.lang = tag;
}

/** Put the tree's language on the document root, where CSS `:lang()` and the
 *  browser's own font-fallback and line-breaking machinery read it. Called on every
 *  render because a payload can arrive with a language the author just switched. */
function applyTreeLang(): void {
    document.documentElement.lang = treeLangTag();
}

/**
 * The toolbar language control: what codoc AUTHORS in, and a menu to change it.
 *
 * It is a switch and not a toggle because there is no natural pair to flip between,
 * and it is labelled with the language's own endonym (简体中文, not "ZH") because that
 * is what a reader of the language recognizes without translating a label first.
 *
 * The wording is deliberate about scope. "Switch" sounds like it changes the
 * document, and it does not: the tree is not retranslated, and a later amend to an
 * existing node still comes out in that node's own language. So the menu says what
 * actually happens — new prose follows the new setting, existing prose does not
 * move — because an author who expects a translation and gets a mixed tree will
 * conclude the feature is broken.
 */
/** The nodes not yet reading as `target` (the webview's honest preview of what
 *  `codoc translate` would pick up — Python's detection stays the authority). */
function nodesNotIn(target: string): string[] {
    const rows = Object.values(payload.nodes).filter(n => !n.isProposal && !n.retired);
    return pendingForTarget(rows, treeLangTag(), target);
}

function renderLangSwitch(): HTMLElement | null {
    const current = treeLangTag();
    // The HOST decides whether switching is on offer. On the deployed hub it is not:
    // a remote contributor suggesting edits has no business changing the language a
    // maintainer's repo is authored in, so the hub sends no choices and this renders
    // as a plain label. Gating on the payload rather than on `isVsCodeHost()` keeps
    // that a host policy instead of a fact the webview asserts about itself.
    const offered = payload.docLanguageChoices ?? [];
    const wrap = el('div', 'tb-lang');
    const translation = payload.translation;

    if (!offered.length) {
        // An English tree with no switch has nothing to say, so say nothing rather
        // than spend toolbar space announcing the default.
        if (current === 'en') return null;
        const label = el('span', 'tb-lang-label', shortLanguageLabel(current));
        label.lang = current;
        label.title = `This tree is authored in ${languageName(current)}.`;
        wrap.append(label);
        return wrap;
    }

    // ── a run in flight: the switcher becomes the progress line ────────────────
    // The count is the honest one (translated / total); the per-node skeletons in
    // both panes show WHICH nodes are still coming. No switch actions mid-run — a
    // second language change while the tree is half-rewritten helps nobody.
    if (translation?.running || translateStarting) {
        const btn = el('button', 'toggle lang translating');
        btn.append(el('span', 'tb-lang-spin'));
        btn.append(document.createTextNode(translation?.running
            ? ` translating ${translation.translated}/${translation.total} → ${shortLanguageLabel(translation.target)}`
            : ' starting translation…'));
        btn.disabled = true;
        btn.title = translation?.running
            ? `codoc translate is rewriting the tree into ${translation.targetName}. `
              + 'Each shimmering section fills in as its batch lands; everything else stays editable.'
            : 'Starting codoc translate — the first batch is on its way.';
        wrap.append(btn);
        return wrap;
    }

    const btn = el('button', 'toggle lang', shortLanguageLabel(current));
    btn.lang = current;
    btn.title = `Authoring language: ${languageName(current)}. codoc writes new `
        + 'titles and descriptions in it. Existing prose is left alone — an amend '
        + 'keeps the language the node is already written in, so a bilingual tree '
        + 'stays bilingual.';
    btn.setAttribute('aria-haspopup', 'true');
    wrap.append(btn);

    const menu = el('div', 'tb-lang-menu');

    const closeMenu = (): void => {
        menu.classList.remove('open');
        btn.setAttribute('aria-expanded', 'false');
    };

    /** Post stage 2 (run `codoc translate`) and flip the switcher into its
     *  starting state until the CLI's first progress write lands. If nothing ever
     *  lands (spawn failed, missing credentials), the spinner must not spin
     *  forever — time out honestly and point at the output channel. */
    const startTranslate = (code: string): void => {
        translateOffer = null;
        translateStarting = true;
        vscode.postMessage({ kind: 'translate-tree', code });
        rerenderToolbar();
        window.setTimeout(() => {
            if (!translateStarting) return;   // progress arrived (or the run ended)
            translateStarting = false;
            rerenderToolbar();
            showTransientNotice('Translation did not start — see the codoc output channel '
                + '(or run `codoc translate` in a terminal).');
        }, 30_000);
    };

    // ── stage 2: the standing offer after a switch ("Translate N nodes?") ──────
    // Held in module state so it survives the toolbar re-render that stage 1's own
    // repost triggers; rendered OPEN so the question is on screen, not behind a
    // click on the very menu that just closed.
    if (translateOffer) {
        const offer = translateOffer;
        menu.classList.add('open');
        btn.setAttribute('aria-expanded', 'true');
        const stage = el('div', 'tb-lang-confirm');
        stage.append(el('div', 'tb-lang-confirm-head',
            `✓ New prose now comes out in ${offer.name}.`));
        stage.append(el('div', 'tb-lang-confirm-body',
            offer.count === 1
                ? '1 existing node is still in another language.'
                : `${offer.count} existing nodes are still in another language.`));
        const row = el('div', 'tb-lang-confirm-actions');
        const go = el('button', 'tb-lang-go',
            offer.count === 1 ? 'Translate it now' : `Translate ${offer.count} nodes now`);
        go.title = 'Runs `codoc translate`: one LLM pass per batch, citations and links '
            + 'kept verbatim. Sections fill in as batches land; previous wording stays '
            + 'in the change ledger (codoc history).';
        go.onclick = ev => { ev.stopPropagation(); startTranslate(offer.code); };
        const later = el('button', 'tb-lang-later', 'Keep them as they are');
        later.title = 'A bilingual tree is fine — you can translate any time from this menu.';
        later.onclick = ev => { ev.stopPropagation(); translateOffer = null; closeMenu(); rerenderToolbar(); };
        row.append(later, go);
        stage.append(row);
        menu.append(stage);
        wrap.append(menu);
        // Click-away / Escape dismisses the offer (it stays reachable as the
        // standing "Translate N nodes" row below). The toolbar re-renders on every
        // payload while the offer stands, so a listener from a REPLACED toolbar may
        // still be attached — it must detach itself without touching the live offer
        // (the current toolbar's own listener handles that).
        const dismiss = (): void => {
            document.removeEventListener('click', dismiss);
            document.removeEventListener('keydown', onKey);
            if (!menu.isConnected) return; // a stale toolbar's listener — not ours to act on
            translateOffer = null;
            closeMenu();
        };
        const onKey = (k: KeyboardEvent): void => { if (k.key === 'Escape') dismiss(); };
        setTimeout(() => {
            document.addEventListener('click', dismiss);
            document.addEventListener('keydown', onKey);
        }, 0);
        return wrap;
    }

    // A language set from the CLI that has no built-in profile still has to appear,
    // or the menu would silently misreport what the tree is authored in.
    const choices = offered.some(c => c.code === current)
        ? offered
        : [{ code: current, name: languageName(current) }, ...offered];
    for (const choice of choices) {
        const item = el('button', 'tb-lang-item' + (choice.code === current ? ' active' : ''));
        item.lang = choice.code;
        item.append(el('span', 'tb-lang-check', choice.code === current ? '✓' : ''));
        item.append(el('span', 'tb-lang-name', choice.name));
        item.onclick = ev => {
            ev.stopPropagation();
            menu.classList.remove('open');
            if (choice.code === current) return;
            // Stage 1: switch what codoc AUTHORS in (instant, config.json). Stage 2
            // is offered, never assumed: translating every description is the one
            // bulk rewrite in codoc, so it waits for its own explicit yes.
            const count = nodesNotIn(choice.code).length;
            translateOffer = count > 0
                ? { code: choice.code, name: choice.name, count }
                : null;
            vscode.postMessage({ kind: 'set-doc-language', code: choice.code });
            if (translateOffer) rerenderToolbar(); // show stage 2 immediately, pre-repost
        };
        menu.append(item);
    }
    // The standing conversion row: a tree with nodes not in its own language can be
    // translated any time — not only in the breath right after a switch.
    const behind = nodesNotIn(current).length;
    if (behind > 0) {
        const item = el('button', 'tb-lang-item translate');
        item.append(el('span', 'tb-lang-check', '⇢'));
        item.append(el('span', 'tb-lang-name',
            behind === 1
                ? `Translate 1 node into ${shortLanguageLabel(current)}`
                : `Translate ${behind} nodes into ${shortLanguageLabel(current)}`));
        item.title = 'Runs `codoc translate` toward the current authoring language. '
            + 'Citations, links and focus spans are kept verbatim; refused nodes are reported.';
        item.onclick = ev => { ev.stopPropagation(); closeMenu(); startTranslate(current); };
        menu.append(item);
    }
    const note = el('div', 'tb-lang-note',
        'Applies to new and re-generated prose. Existing nodes keep their own language.');
    menu.append(note);
    wrap.append(menu);

    btn.onclick = ev => {
        ev.stopPropagation();
        const open = menu.classList.toggle('open');
        btn.setAttribute('aria-expanded', String(open));
        if (open) {
            // One dismissal path for click-away and Escape — a menu that can only be
            // closed by picking something is a trap when the reader only wanted to see
            // which language the tree is in.
            const close = () => {
                menu.classList.remove('open');
                btn.setAttribute('aria-expanded', 'false');
                document.removeEventListener('click', close);
                document.removeEventListener('keydown', onKey);
            };
            const onKey = (k: KeyboardEvent) => { if (k.key === 'Escape') close(); };
            setTimeout(() => {
                document.addEventListener('click', close);
                document.addEventListener('keydown', onKey);
            }, 0);
        }
    };
    return wrap;
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

    // History (blame) toggle (W2): each feature shows who last changed it + when,
    // with an author-role attribution rail. Decoration only; persisted per workspace.
    const blame = el('button', 'toggle blame' + (prefs.blame ? ' active' : ''),
        (prefs.blame ? '◉ ' : '◎ ') + 'History');
    blame.title = prefs.blame
        ? 'History on — each feature shows who last changed it and when. Hover for the full trace. Click to hide.'
        : 'History — show who last changed each feature, when, and why (blame)';
    blame.setAttribute('aria-pressed', String(!!prefs.blame));
    blame.onclick = () => { setPref('blame', !prefs.blame); applyBlame(); rerenderToolbar(); };
    actions.append(blame);

    const langSwitch = renderLangSwitch();
    if (langSwitch) actions.append(langSwitch);

    // ── catch-up: descriptions the loop rewrote while you were elsewhere ──────
    // The only automatic op that changes what the document says, and nobody was
    // asked. This is deliberately a LINE, not a panel: a log is a place you have to
    // remember to visit, and nobody visits it. It appears only when something is
    // owed, walks you to each one, and disappears for good as they are read.
    const autoEdits = payload.autoEdits ?? {};
    const unseen = unseenEdits(autoEdits, new Set(), docOrderFids());
    if (unseen.length) {
        actions.append(el('span', 'tb-sep'));
        const pill = el('button', 'toggle autoedits');
        pill.append(icon('arrows-clockwise'), document.createTextNode(' ' + catchUpLabel(unseen)));
        pill.title = 'codoc changed these descriptions itself, to match the code. '
            + 'Click to step through them — each shows the change in place with '
            + 'Keep / Restore, and clears when you decide.';
        pill.onclick = () => {
            // Walk to the next one PAST the current selection so repeated clicks
            // advance rather than bouncing on the first.
            const after = unseen.findIndex(u => u.fid === selectedId);
            setSelected(unseen[(after + 1) % unseen.length].fid, true);
        };
        actions.append(pill);

        // Keep all — acknowledge the lot without changing a word.
        //
        // One at a time is right when the loop rewrites a description here and
        // there. It is not right after `codoc translate`, which rewrites EVERY
        // node: the reader is then asked for twenty-five verdicts on a rewrite
        // they asked for by name, and the honest answer to each is the same.
        //
        // Safe in a way Accept-all is not, and for a structural reason: Keep is
        // the verdict that changes nothing. It records that the rewrite was seen
        // and leaves the prose exactly as it is. Restore is the one that writes,
        // and it stays per-node, because reverting twenty-five descriptions in one
        // click is a different and much worse button.
        //
        // The one thing it can hide is a rewrite that displaced the author's OWN
        // wording, which the store holds to a stricter bar and this surface draws
        // heavier. Those are counted on the button, the same way Accept-all counts
        // the proposals that ask the agent to write code.
        const verdicts = keepAllVerdicts(unseen);
        const mine = unseen.filter(u => u.edit.written_by === 'human').length;
        const keepAll = el('button', 'toggle bulk');
        keepAll.append(document.createTextNode(keepAllLabel(unseen)));
        keepAll.title = (mine
            ? `${mine} of these replaced wording you wrote yourself. Keeping them `
              + 'acknowledges the change; it does not undo it, and Restore stays '
              + 'available on each one until you decide.'
            : 'All of these are codoc revising its own earlier wording.')
            + ' Keep changes nothing — it only clears the marks.';
        keepAll.onclick = () => {
            for (const v of verdicts) vscode.postMessage({ kind: 'auto-edit-verdict', ...v });
        };
        actions.append(keepAll);
    }

    const ids = payload.pendingEventIds;
    if (ids.length) {
        actions.append(el('span', 'tb-sep'));
        // Accept-all is the one click that can resolve a proposal the reader never
        // looked at, so it is the one place a hidden consequence does the most damage:
        // "Accept all (7)" gave no hint that two of the seven ask the agent to write or
        // delete code. Count them and say so on the button itself.
        const sending = ids.filter(id => leavesForAgent(consequenceForEvent(id)));
        const accAll = el('button', 'toggle bulk' + (sending.length ? ' sends' : ''));
        if (sending.length) accAll.append(icon('paper-plane-tilt'));
        accAll.append(document.createTextNode(sending.length
            ? ` Accept all (${ids.length}, ${sending.length} to build)`
            : `✓ Accept all (${ids.length})`));
        const origins = originBreakdown(ids);
        accAll.title = (sending.length
            ? `${ids.length} pending — ${sending.length} of them ask the agent to write or `
              + 'delete code. The rest only update the tree\'s wording.'
            : `${ids.length} pending — all of them only update the tree's wording. No code changes.`)
            + (origins ? ` (${origins}.)` : '');
        accAll.onclick = () => { beginApplying(null); postVerdict(ids.slice(), true); };
        const rejAll = el('button', 'toggle bulk', `✗ Reject all (${ids.length})`);
        rejAll.title = 'Discard every pending proposal. Nothing is written.';
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

/**
 * A proposed node, drawn as the node it will become rather than as a card about it.
 *
 * An ADD used to render as a blue "+ new <title>  Reject  Accept" strip wedged under
 * the destination parent's heading, which read as an edit to that parent's own text.
 * A node that does not exist yet is a placeholder, so it is drawn as one: the same
 * dimmed italic title an accepted-but-unbuilt feature already wears, at the position
 * it will occupy. Accepting it changes almost nothing on screen, which is exactly
 * what accepting it means. The verdict rides the row's hover like every other row's.
 */
function appendGhostRow(parent: HTMLElement, n: UINode): void {
    const op = n.proposalOp || 'add';
    const row = el('div', 'row proposal ' + op);
    applyNodeLang(row, n);
    row.dataset.id = n.id;
    row.style.setProperty('--depth', String(n.depth));
    if (selectedId === n.id) row.classList.add('selected');
    // A move is a relocation of something that exists (keep the → kind glyph); an add
    // is a placeholder, and its dimming already says so — no glyph.
    if (op === 'move') row.append(el('span', 'pglyph', kindGlyph('move')));
    const t = el('span', 'title ghost-title');
    t.textContent = n.title || '(untitled)';
    const cq = nodeConsequence(n.proposal);
    row.title = op === 'move'
        ? 'The agent proposes moving this feature here. Nothing has moved yet.'
        : 'The agent proposes this feature. ' + consequenceNote(cq);
    row.append(t);
    if (n.proposal?.verdictPending) row.append(verdictWaiting());
    else if (n.proposal) row.append(verdictButtons(n.proposal.eventId, cq));
    row.onclick = () => setSelected(n.id, true);
    parent.append(row);
}

function appendRow(parent: HTMLElement, id: string): void {
    const n = payload.nodes[id];
    if (!n) return;
    if (n.isProposal) { appendGhostRow(parent, n); return; }

    const row = el('div', 'row');
    row.dataset.id = id;
    applyNodeLang(row, n);
    if (selectedId === id) row.classList.add('selected');
    if (n.retired) row.classList.add('retired');
    if (!n.realized) row.classList.add('unrealized');
    // Being rewritten right now (translation batch pending / agent applying) — the
    // same skeleton read the doc pane wears, so both panes tell one story.
    if (busyByFid[id]) { row.classList.add('busy'); row.title = busyByFid[id].label; }
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
    // ONE badge, ONE state (feature-state.ts). A feature used to wear up to six markers
    // at once — activity dot, captured circle, queued diamond, divergence warning,
    // unrealized ring, amend/retire chip — all true, none ranked, so the row read as a
    // legend nobody had. They are stages of one lifecycle; only the furthest-along one
    // changes what to do next, and it is the only one drawn.
    const signals = {
        activeMode: n.activeMode,
        proposalOp: n.proposal?.op ?? null,
        divergent: !!divergent[id],
        sent: awaitingAI.has(id) && !draftSet.has(id),
        staged: draftSet.has(id),
        realized: n.realized,
        queuedIntent: (payload.holdDetail ?? {})[id]?.intent,
    };
    const state = featureState(signals);
    titleWrap.title = state === 'planned' ? PLANNED_TITLE : 'Open in the document editor';
    // Titles are edited in the whole-doc editor now — double-click just scrolls there.
    titleWrap.ondblclick = ev => { ev.stopPropagation(); setSelected(id, true); };
    row.append(titleWrap);

    if (n.proposal?.op === 'amend' && n.proposal.title && n.proposal.title !== n.title) {
        row.append(el('span', 'amend-inline', '→ ' + n.proposal.title));
    }

    const badge = stateBadge(state, signals);
    if (badge) {
        const b = el('span', 'badge ' + badge.cls);
        b.title = badge.title;
        if (badge.icon) b.append(icon(badge.icon));
        row.append(b);
    }

    // (code refs moved into the document's inline "threads" line under each heading — U4)

    // One resolution surface per feature: the row's own hover-revealed verdict, for
    // whatever the agent proposes on it. Add/move proposals carry theirs on their own
    // placeholder row (appendGhostRow), so they are not repeated here.
    if (n.proposal && (n.proposal.op === 'amend' || n.proposal.op === 'retire')) {
        if (n.proposal.verdictPending) row.append(verdictWaiting());
        else row.append(verdictButtons(n.proposal.eventId, nodeConsequence(n.proposal)));
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
            // The GESTURE, not the command: in VS Code the host turns it into a move
            // command (it mints the emission token); on the hub the client emits it.
            if (commands) {
                const cmd = moveCommand(dragSourceId, id, commands.token());
                commands.record([cmd]);
                vscode.postMessage(commandMessage(cmd));
            } else {
                vscode.postMessage({ kind: 'tree-move', sourceId: dragSourceId, newParentId: id });
            }
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
    askBar?.destroy(); askBar = null;
    findView?.destroy(); findView = null;
    computeFocus(null); // editor torn down → clear the dependency-spotlight body class
    if (!payload.doc) {
        host.append(el('div', 'doc empty', 'No features yet. Run `codoc init` to bootstrap the tree.'));
        return host;
    }
    askBar = createAskBar({
        onStep: fid => wholeEditor?.goToAskStep(fid),
        onDismiss: () => {
            // Suppress THIS walkthrough by id so an in-flight payload can't restore it
            // between the click and the file actually going away — the dismiss feels
            // instant on every host, and a suggest-only hub viewer (who cannot delete
            // the shared file) still gets to hide it for themselves.
            dismissedAskId = payload.ask?.id ?? '';
            askBar?.setWalkthrough(null);
            wholeEditor?.setAsk(null);
            applyAskLayout(host);
            // Delete the shared file only when this viewer may — VS Code always; the
            // hub only for a hand-off collaborator (dispatch gates it too, this just
            // avoids a dead 403 and the overlay flickering back for a read-only viewer).
            if (canDismissAsk()) vscode.postMessage({ kind: 'ask-dismiss' });
        },
    });
    host.append(askBar.element);
    wholeEditor = mountWholeDocEditor(host, {
        controller: authorController,
        getSymbols: () => payload.symbols ?? [],
        // Cite the projection this editor was rendered from (#4) so the host diffs the
        // settle against that exact baseline, not a newer projection that landed in flight.
        // The EDITOR supplies the citation, not `payload` — see WholeDocEditorOptions.
        // A settle flushed by an arriving projection carries pre-adoption content, and
        // `payload` has already been reassigned to the new projection by then, so reading
        // it here stamped the wrong baseline and turned the daemon's in-flight edits into
        // user edits that revert them (#2).
        onSettle: (doc, baselineId) => {
            if (commands) { postCommands(commands.settle(doc, baselineId)); return; }
            vscode.postMessage({ kind: 'doc-settle', doc, baselineId });
        },
        onCommit: (doc, baselineId) => {
            fireSaveShimmer();
            if (!commands) { vscode.postMessage({ kind: 'commit', doc, baselineId }); return; }
            // Hub: commit is settle + (if this viewer may) hand off. The capability gate
            // is server-side; checking it here only avoids a refusal notice for a
            // suggest-only contributor, whose edits are recorded and held either way.
            postCommands(commands.settle(doc, baselineId));
            if (payload.viewer?.canHandOff) vscode.postMessage({ kind: 'hand-off' });
        },
        // `edits` — the author reshaped an editable ghost before accepting; the
        // daemon applies the proposal with the edited title/description.
        onAccept: (s, edits) => { if (s.eventId) { beginApplying(null); postVerdict([s.eventId], true, edits); } },
        onReject: s => { if (s.eventId) { beginApplying(null); postVerdict([s.eventId], false); } },
        onWithdrawRealization: featureId => vscode.postMessage({ kind: 'withdraw-realization', featureId }),
        onOpenBinding: (file, symbol) => vscode.postMessage({ kind: 'open-binding', file, symbol }),
        onConsult: url => vscode.postMessage({ kind: 'open-link', url }),
        onCommentCreate: (doc, thread, media) => vscode.postMessage({ kind: 'comment-create', doc, thread, mediaData: media?.data, mediaMime: media?.mime }),
        onCommentEdit: (id, body) => vscode.postMessage({ kind: 'comment-edit', id, body }),
        onCommentResolve: (doc, id) => vscode.postMessage({ kind: 'comment-resolve', doc, id }),
        // Keep/Restore on an unasked loop rewrite. Either way the host records the
        // acknowledgement; a Restore additionally re-authors the previous wording
        // through the ordinary command channel (a real edit — held until hand-off
        // if the daemon classifies it code-implying).
        onAutoEditVerdict: (fid, at, keep, prev) =>
            vscode.postMessage({ kind: 'auto-edit-verdict', fid, at, keep, prev }),
        onActiveFeature: (fid, source) => {
            if (!fid) { onBridgeCaretLeave(); return; }
            // Caret moved to a different feature without an intervening edit → clear the
            // code-side bridge highlight (the pane stays open, §A.1). An edit re-opens it.
            if (fid !== bridgeFid) onBridgeCaretLeave();
            syncingFromEditor = true;
            setSelected(fid, false); // highlight the tree row, don't re-scroll the editor
            syncingFromEditor = false;
            // Reading down the document past a stop advances the counter, so "3 of 7"
            // stays true whether the reader used the stepper or just scrolled.
            askBar?.syncActive(fid);
            // Eased re-center ONLY on the scroll-driven spy — a caret move (source==='selection')
            // just highlights, else typing would animate the tree on every keystroke (KTD2).
            if (shouldCenter(source)) centerTreeRow(fid);
        },
        onEditFeature: fid => onBridgeEdit(fid),  // P2 doc→code (§A.1), debounced below
        onHoverFeature: fid => peekTreeRow(fid), // WS5: preview a dependency link's target
        onBlockEdit: edit => vscode.postMessage({ kind: 'block-edit', block: edit }),  // v6
        onFindUpdate: state => findView?.render(state),
    });
    findView = createFindView({
        onSearch: (q, o) => wholeEditor?.setFindQuery(q, o) ?? { count: 0, index: -1, query: q },
        onStep: d => wholeEditor?.stepFind(d) ?? { count: 0, index: -1, query: '' },
        onReplace: (r, pc) => wholeEditor?.replaceFind(r, pc) ?? { count: 0, index: -1, query: '' },
        onReplaceAll: (r, pc) => wholeEditor?.replaceAllFind(r, pc) ?? 0,
        onClose: () => wholeEditor?.setFindOpen(false),
    });
    host.append(findView.element);
    wholeEditor.setSuggestions(payload.suggestions ?? []); // before setDoc — see reconcile()
    wholeEditor.setThreads(payload.threads ?? {});
    wholeEditor.setPhases(payload.sync.phase ?? {});
    wholeEditor.setSteps(payload.sync.steps ?? {});
    wholeEditor.setAutoEdits(payload.autoEdits ?? {});
    wholeEditor.setBusy(busyByFid);   // skeleton shimmer + per-section edit guard
    wholeEditor.setHeld(handedOff(payload), payload.holdDetail ?? {});
    wholeEditor.setDrafts(payload.drafts ?? []);
    wholeEditor.setBlocks(payload.blocks ?? {});
    wholeEditor.setComments(payload.comments ?? []);
    wholeEditor.setHoverCards(payload.hoverCards ?? null);
    wholeEditor.setMintedMap(payload.mintedByLocalId ?? {});  // before setDoc — exact fid reconcile
    wholeEditor.setHistory(payload.history ?? {});   // W2 blame data
    wholeEditor.setDoc(payload.doc, payload.baselineId);
    applyAsk(host);     // seed the /codoc:ask overlay, if one is up
    applyGlance();      // seed pitch + glance state into the fresh editor
    applyBlame();       // seed the History stance into the fresh editor
    // Presence rides the doc surface — re-place the avatar as the surface scrolls (the agent's
    // heading moves under a static avatar). The surface is recreated here, so re-bind.
    host.querySelector<HTMLElement>('.ce-whole-surface')
        ?.addEventListener('scroll', () => presence.reposition(), { passive: true });
    return host;
}

// ─── /codoc:ask walkthrough ───────────────────────────────────────────────────
/** The bar only occupies space when there IS a walkthrough — `.doc-host` stays a
 *  plain single-child row otherwise, so nothing about the ordinary layout changes
 *  for readers who never ask anything. */
function applyAskLayout(host?: HTMLElement | null): void {
    const h = host ?? document.querySelector<HTMLElement>('.doc-host');
    h?.classList.toggle('has-ask', !!payload.ask);
}

/** Push the current payload's walkthrough into the bar and the editor. Called on
 *  first render and on every reconcile, so an ask that lands while the reader is
 *  mid-edit simply appears, and `codoc_walkthrough_clear` makes it vanish. */
function applyAsk(host?: HTMLElement | null): void {
    // A walkthrough the reader dismissed stays gone even though the file may not be
    // deleted yet (or, for a read-only hub viewer, at all) — until a genuinely NEW
    // question replaces it.
    const walk = (payload.ask && payload.ask.id !== dismissedAskId) ? payload.ask : null;
    askBar?.setWalkthrough(walk);
    wholeEditor?.setAsk(walk);
    applyAskLayout(host);
    // Land on the first stop when a NEW walkthrough arrives — an answer nobody is
    // taken to the start of is a list of numbers.
    const first = askBar?.currentFid();
    if (walk && first && walk.id !== lastAskId) wholeEditor?.goToAskStep(first);
    lastAskId = walk?.id ?? '';
}
let lastAskId = '';
let dismissedAskId = '';

/** Whether this viewer may take the shared walkthrough down for everyone. VS Code
 *  has no viewer block → full authority; on the hub only a hand-off collaborator
 *  may, matching dispatch.py's gate — a suggest-only contributor sees it and
 *  dismisses it locally. */
function canDismissAsk(): boolean {
    return payload.viewer?.canHandOff !== false;
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
    // ⌘F / ⌘⌥F (Ctrl+F / Ctrl+H elsewhere). Capture phase for the same reason ⌘S
    // is captured: a webview never receives VS Code's Find widget — the keystroke
    // arrives here as a plain DOM event and, unclaimed, does nothing at all.
    if (findView && openFindChord(ev, IS_MAC)) {
        ev.preventDefault();
        ev.stopPropagation();
        openFind(replaceChord(ev, IS_MAC));
        return;
    }
    if (findView?.onKeydown(ev)) { ev.stopPropagation(); return; }
    if ((IS_MAC ? ev.metaKey : ev.ctrlKey) && (ev.key === 'k' || ev.key === 'K')) {
        ev.preventDefault();
        ev.stopPropagation();
        palette.toggle();
    }
}, true);

/** ⌘F (mac) / Ctrl+F — and Ctrl+H, which is Replace on Windows and Linux. */
function openFindChord(ev: KeyboardEvent, mac: boolean): boolean {
    const accel = mac ? ev.metaKey : ev.ctrlKey;
    if (!accel) return false;
    const k = ev.key.toLowerCase();
    return k === 'f' || (!mac && k === 'h');
}

/** Whether the chord asks for the replace row: ⌘⌥F on mac, Ctrl+H elsewhere. */
function replaceChord(ev: KeyboardEvent, mac: boolean): boolean {
    return mac ? ev.altKey : ev.key.toLowerCase() === 'h';
}

/** Open find, seeded from the editor selection the way ⌘F does everywhere. */
function openFind(replace: boolean): void {
    if (!findView || !wholeEditor) return;
    wholeEditor.setFindOpen(true);
    const seed = (window.getSelection()?.toString() ?? '').trim();
    // A multi-line selection is a passage, not a search term — seeding it would put
    // a paragraph in the field and report no matches.
    findView.open({ replace, seed: seed.includes('\n') ? '' : seed.slice(0, 120) });
}

// ⌘S / Ctrl-S = "save the file" from ANY focus context (nav-tree pane, toolbar, editor,
// anywhere in the webview) → stage & send (commit) (U6 / R11, R12). `tree.codoc` is a
// derived, read-only export; the daemon is the sole writer and the host never dirties the
// backing text document, so the native save would only ever flash the "content is newer"
// dialog. Capture phase + stopPropagation means this fires before the in-editor ProseMirror
// `Mod-s` keymap (which listens on the deeper editable element), so the commit runs exactly
// once and the native save never reaches VS Code. The in-editor binding (whole-doc-editor.ts)
// stays as a harmless fallback for the (unreachable-here) bubble path.
//
// Phase A note: there is no clean VS Code host-API flag to mark a CustomTextEditor's backing
// document non-savable without a full FileSystemProvider rewrite (out of scope per U6), so this
// window-level interceptor IS the accepted Phase A read-only mechanism. AE3 (no save dialog) is
// verified manually.
window.addEventListener('keydown', ev => {
    if (isSaveChord(ev, IS_MAC)) {
        ev.preventDefault();
        ev.stopPropagation();
        triggerCommit();
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
/** Code→doc nav reveal that arrived before the first projection painted —
 *  applied once the payload lands so a freshly-opened editor still navigates. */
let pendingReveal: string | null = null;

/** Resolve a reveal target to a live node id: by fid first, else by exact title
 *  (the public navigateToFeature command historically accepted titles). */
function resolveRevealTarget(idOrTitle: string): string | null {
    if (payload.nodes[idOrTitle]) return idOrTitle;
    for (const [id, n] of Object.entries(payload.nodes)) {
        if (n.title === idOrTitle) return id;
    }
    return null;
}

window.addEventListener('message', ev => {
    const msg = ev.data as { kind: string; payload: DocPayload; fids?: string[]; big?: string[]; fid?: string };
    // Code→doc spark (P2 / §A.3): a bound source file was edited — land the inbound glyph on
    // each touched heading and pulse its tree row, even when that section is scrolled off.
    if (msg.kind === 'code-touch') { onCodeTouch(msg.fids ?? [], msg.big ?? []); return; }
    // Hub only: what is happening to the things this client sends. Arrives as a
    // window message like everything else, so there is one inbound path.
    if (msg.kind === 'delivery') {
        viewerStatus?.setDelivery((ev.data as { delivery: Delivery }).delivery);
        return;
    }
    // W3: a prose-only edit committed live to the tree (daemon echoed it back,
    // no directive minted) — flash a quiet "saved" confirmation on the heading
    // so the edit doesn't vanish into silence.
    if (msg.kind === 'saved-flash') { onSavedFlash(msg.fids ?? []); return; }
    // The `codoc.find` command — ⌘F pressed while focus sits outside the webview
    // (the tree pane, the toolbar), where the DOM listener above never sees it.
    if (msg.kind === 'find') { openFind(!!(ev.data as { replace?: boolean }).replace); return; }
    // Code→doc navigation (the source CodeLens): select + scroll to the feature.
    if (msg.kind === 'reveal-feature') {
        const fid = msg.fid;
        if (!fid) return;
        if (!mounted) { pendingReveal = fid; return; }
        const resolved = resolveRevealTarget(fid);
        if (!resolved) { pendingReveal = fid; return; }  // await a fresher projection
        setSelected(resolved, true);
        return;
    }
    if (msg.kind !== 'doc') return;
    if (msg.payload.rev < lastRev) return; // ignore stale posts
    lastRev = msg.payload.rev;
    payload = msg.payload;
    // Record the projection as a citable baseline BEFORE anything renders from it, so a
    // settle flushed by the render below can still resolve what it cites (hub only).
    commands?.observe(payload);
    viewerStatus?.setViewer(payload.viewer);
    awaitingAI = new Set(payload.awaitingAI ?? []);
    draftSet = new Set(payload.drafts ?? []);
    divergent = payload.divergent ?? {};
    busyByFid = busyFromPayload(payload);
    // The CLI's first progress write landed (or the run already ended) — the
    // "starting…" spinner has done its job either way.
    if (payload.translation) translateStarting = false;
    // A finished run with refusals owes the author one honest line (the full
    // reasons are in the codoc output channel / `codoc translate` output).
    maybeNoticeSkips(payload.translation);
    // The offer's moment has passed if a run is now underway, or the workspace
    // language moved to something other than what the offer targeted.
    if (translateOffer && (payload.translation?.running
        || (payload.docLanguage?.code && payload.docLanguage.code !== translateOffer.code))) {
        translateOffer = null;
    }
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
    if (pendingReveal) {
        const resolved = resolveRevealTarget(pendingReveal);
        if (resolved) { setSelected(resolved, true); pendingReveal = null; }
    }
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
