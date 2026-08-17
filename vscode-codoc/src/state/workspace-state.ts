/**
 * WorkspaceState — the file-based bridge to the codoc daemon (no HTTP server).
 *
 * Watches .codoc/{tree.codoc, tree.bindings.json, status.json, inbox.json};
 * reparses on change; fires onDidChange so providers refresh without polling.
 *
 * Status bar reflects .codoc/status.json's lifecycle state:
 *   $(loading~spin) implementing…   – realizing  (agent writing code)
 *   $(pencil) applying tree edits…  – tree_dirty (codoc edited, code pending)
 *   $(play) N to implement          – awaiting_impl (accepted edits queued in
 *                                      .codoc/realize.md for the live session)
 *   $(bell) N proposals             – code_drift (code changed, review pending)
 *   $(check) N                      – in_sync
 *   $(sync) not initialized         – no .codoc dir
 */

import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { parseTreeCodoc, ParsedFeature, ProposalHunk } from './tree-model';
import { SidecarData, emptySidecar, featureAdjacency } from './bindings-model';
import { RegistryData } from './registry-model';
import { loadRegistry } from './registry-loader';
import { ActivityData, parseActivity, isAgentActive, computeActiveFeatureLines, EPOCH_UI_TTL_MS } from './activity-model';
import { parseRealize, pendingCodeByFile, PendingChange, parseRealizedLog, newOutcomes, RealizedOutcome } from './realize-model';
import { statusBarView } from './status-presentation';
import { leaseStatus, realizeQueueSize, REALIZING_LEASE_MS, daemonUnresponsive, HOST_LOG_GRACE_MS } from './status-model';
import { parseTranslateProgress } from './translate-model';
import { parseAsk, ASK_TTL_MS, type AskWalkthrough } from './ask-model';
import type { TranslationProgress } from '../webview/protocol';

export { ParsedFeature, SidecarData };

export interface CodocStatus {
    state: 'in_sync' | 'code_drift' | 'tree_dirty' | 'awaiting_impl' | 'realizing';
    pending: number;
    detail: string;
}

export class WorkspaceState {
    readonly statusBar: vscode.StatusBarItem;
    private _rootDir: string | null = null;
    private _features: ParsedFeature[] = [];
    private _proposals: ProposalHunk[] = [];
    private _sidecar: SidecarData = emptySidecar();
    private _registry: RegistryData | null = null;
    private _status: CodocStatus = { state: 'in_sync', pending: 0, detail: '' };
    private _activity: ActivityData = {};
    // activity.json's last-modified time — the epoch/phase lease's `last_seen`
    // (see activity-model.ts). Undefined when the file is unreadable.
    private _activityMtimeMs: number | undefined;
    // status.json's last-modified time — the realizing lease's `last_seen`
    // (see status-model.ts). Undefined when the file is unreadable.
    private _statusMtimeMs: number | undefined;
    private _hostLogBytes = 0;
    private _hostLogMtimeMs: number | undefined;
    private _statusNeverWritten = false;
    private _pendingCode: Map<string, PendingChange[]> = new Map();
    // `codoc translate` progress (lease-guarded; null when no run is in play).
    private _translation: TranslationProgress | null = null;
    // The /codoc:ask walkthrough overlay. Read like any other control file, but it
    // is the only one that is purely a VIEW — nothing derives from it, so a corrupt
    // or absent ask.json costs the reader an overlay and nothing else.
    private _ask: AskWalkthrough | null = null;
    private _provisioning = false;
    // One-shot timer that re-derives state when a TTL lease (realizing / agent
    // epoch) expires with no file event to trigger a reload — see
    // _scheduleLeaseExpiry (review #10).
    private _leaseTimer: ReturnType<typeof setTimeout> | undefined;
    // Debounce timer coalescing the burst of file-watcher events one loop pass
    // emits (~6 .codoc/* writes back-to-back) into a single reload — see _init.
    private _reloadTimer: ReturnType<typeof setTimeout> | undefined;

    private _onDidChange = new vscode.EventEmitter<void>();
    readonly onDidChange = this._onDidChange.event;

    /** Fires with directive outcomes NOT yet surfaced to the user (delta vs a
     *  memento-persisted seen set — a window reload never re-fires old ones).
     *  extension.ts turns these into completion notifications. */
    private _onDidRealize = new vscode.EventEmitter<RealizedOutcome[]>();
    readonly onDidRealize = this._onDidRealize.event;

    constructor(private context: vscode.ExtensionContext) {
        this.statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
        this.statusBar.command = 'codoc.open';
        context.subscriptions.push(this.statusBar);
        context.subscriptions.push({ dispose: () => this._clearLeaseTimer() });
        this._init();
    }

    private _init(): void {
        this._rootDir = this.detectRootDir();
        this._reload();

        // One Loop B / Loop A pass rewrites up to ~6 of these files back-to-back; firing
        // a full reparse+repaint per file gives ~6 redundant reloads for one logical
        // change. Coalesce them behind a short debounce so a burst collapses into a
        // single reload (the daemon's writes within a pass land well inside this window).
        const reload = (): void => {
            if (this._reloadTimer) clearTimeout(this._reloadTimer);
            this._reloadTimer = setTimeout(() => {
                this._reloadTimer = undefined;
                this._rootDir = this.detectRootDir();
                this._reload();
            }, 60);
        };
        for (const glob of [
            '**/.codoc/tree.codoc',
            '**/.codoc/tree.doc.json',  // KTD9: daemon-written store projection — repaint the webview
            '**/.codoc/tree.bindings.json',
            '**/.codoc/tree.index.json',
            '**/.codoc/status.json',
            '**/.codoc/inbox.json',
            '**/.codoc/activity.json',
            '**/.codoc/realize.md',
            '**/.codoc/realized.jsonl',  // directive outcomes → completion notifications
            '**/.codoc/config.json',     // authoring language — changed by `codoc lang` too,
                                        // so a switch made in the terminal repaints the view
            '**/.codoc/translate.json',  // `codoc translate` progress — per-batch skeleton updates
            '**/.codoc/ask.json',        // the /codoc:ask walkthrough overlay — a pure view,
                                         // written by the MCP tool and deleted to dismiss
            '**/.codoc/edits.host.jsonl', // the IDE's own append log: its lifetime is the
                                          // daemon-liveness signal (status-model.daemonUnresponsive)
        ]) {
            const w = vscode.workspace.createFileSystemWatcher(glob);
            this.context.subscriptions.push(w, w.onDidChange(reload), w.onDidCreate(reload), w.onDidDelete(reload));
        }
        this.context.subscriptions.push({ dispose: () => { if (this._reloadTimer) clearTimeout(this._reloadTimer); } });
    }

    detectRootDir(): string | null {
        const cfg = vscode.workspace.getConfiguration('codoc');
        const manual: string = cfg.get('rootDir', '');
        if (manual && fs.existsSync(path.join(manual, '.codoc'))) return manual;
        for (const folder of vscode.workspace.workspaceFolders ?? []) {
            if (fs.existsSync(path.join(folder.uri.fsPath, '.codoc'))) return folder.uri.fsPath;
        }
        return null;
    }

    private _codocPath(name: string): string {
        return path.join(this._rootDir!, '.codoc', name);
    }

    private _reload(): void {
        // codoc.ready drives the walkthrough's onContext completion: true whenever a
        // `.codoc/` repo is present (already set up), so the step ticks without a
        // fresh `codoc.setup` run this session.
        void vscode.commands.executeCommand('setContext', 'codoc.ready', this._rootDir !== null);
        if (!this._rootDir) {
            this._clearLeaseTimer();
            this._features = [];
            this._proposals = [];
            this._sidecar = emptySidecar();
            this._registry = null;
            this._status = { state: 'in_sync', pending: 0, detail: '' };
            this._pendingCode = new Map();
            this._updateStatusBar();
            this._onDidChange.fire();
            return;
        }

        try {
            const parsed = parseTreeCodoc(fs.readFileSync(this._codocPath('tree.codoc'), 'utf-8'));
            this._features = parsed.features;
            this._proposals = parsed.proposals;
        } catch {
            this._features = [];
            this._proposals = [];
        }

        try {
            this._sidecar = JSON.parse(fs.readFileSync(this._codocPath('tree.bindings.json'), 'utf-8')) as SidecarData;
        } catch {
            this._sidecar = emptySidecar();
        }

        // tree.index.json — the cross-reference registry (resolved/dead refs).
        // Tolerant: missing/corrupt → null (loadRegistry never throws).
        this._registry = loadRegistry(this._rootDir);

        // .codoc/realize.md → which code the queued tree edits will touch (reverse
        // direction: codoc → codebase placeholders). Absent when nothing queued.
        // Read once here: the same text seeds both the pending-code map and the
        // realizing lease's queue-present decay below.
        let realizeText = '';
        try {
            realizeText = fs.readFileSync(this._codocPath('realize.md'), 'utf-8');
        } catch { /* nothing queued */ }
        this._pendingCode = pendingCodeByFile(parseRealize(realizeText));

        this._statusMtimeMs = undefined;
        this._statusNeverWritten = false;
        try {
            const statusPath = this._codocPath('status.json');
            const st = JSON.parse(fs.readFileSync(statusPath, 'utf-8'));
            this._statusMtimeMs = fs.statSync(statusPath).mtimeMs;
            // Lease-decay a crashed `realizing` pass (review #9): the IDE never
            // runs the daemon's refresh_status, so without this a killed
            // /codoc:sync would spin "implementing…" indefinitely.
            this._status = leaseStatus(
                { state: st.state, pending: st.pending ?? 0, detail: st.detail ?? '' },
                this._statusMtimeMs,
                realizeQueueSize(realizeText),
            );
        } catch {
            // No status file at all. Only the daemon writes one (every watch pass,
            // and the CLI), so its absence in an initialized repo means no daemon
            // has EVER run here — which is exactly what a freshly-unpacked study
            // workspace is. Deriving "in_sync" from the proposal count here is
            // how a machine with a dead daemon showed a green check for a whole
            // pilot session. Mark it instead; the pill says what to start.
            const n = this._proposals.length;
            this._status = { state: n ? 'code_drift' : 'in_sync', pending: n, detail: '' };
            this._statusNeverWritten = true;
        }

        // The daemon-liveness signal: our own append log's size and age. A live
        // daemon consumes it within one Loop B pass; a log that sits there past
        // the grace means every status read above came from a file nobody is
        // updating — including the frozen `in_sync` a study archive ships with.
        this._hostLogBytes = 0;
        this._hostLogMtimeMs = undefined;
        try {
            const st = fs.statSync(this._codocPath('edits.host.jsonl'));
            this._hostLogBytes = st.size;
            this._hostLogMtimeMs = st.mtimeMs;
        } catch { /* absent → consumed or never written; either way the daemon is not owed */ }

        let activityText = '';
        this._activityMtimeMs = undefined;
        try {
            const activityPath = this._codocPath('activity.json');
            activityText = fs.readFileSync(activityPath, 'utf-8');
            this._activityMtimeMs = fs.statSync(activityPath).mtimeMs;
        } catch { /* file absent → no active agent */ }
        this._activity = parseActivity(activityText);

        // `codoc translate` progress — the per-node skeleton set + toolbar line.
        // Read fresh each reload (the CLI rewrites it per batch); lease-guarded so a
        // crashed run's stale `running: true` never skeleton-locks the editor.
        this._translation = null;
        try {
            const tp = this._codocPath('translate.json');
            this._translation = parseTranslateProgress(
                fs.readFileSync(tp, 'utf-8'), fs.statSync(tp).mtimeMs, Date.now());
        } catch { /* no run in play */ }

        // `.codoc/ask.json` — the walkthrough overlay, expired the same way the
        // Python reader expires it (mtime, not the recorded time) so a clock change
        // cannot resurrect yesterday's question.
        this._ask = null;
        try {
            const ap = this._codocPath('ask.json');
            const age = Date.now() - fs.statSync(ap).mtimeMs;
            if (age <= ASK_TTL_MS) this._ask = parseAsk(JSON.parse(fs.readFileSync(ap, 'utf-8')));
        } catch { /* absent, corrupt, or unreadable → no overlay */ }

        this._updateStatusBar();
        this._onDidChange.fire();
        this._scheduleLeaseExpiry();
        this._surfaceRealized();
    }

    /** Diff realized.jsonl against the persisted seen-id set and fire the delta.
     *  First run in a workspace (no memento) seeds silently — upgrading must not
     *  toast the whole backlog. */
    private _surfaceRealized(): void {
        let text = '';
        try {
            text = fs.readFileSync(this._codocPath('realized.jsonl'), 'utf-8');
        } catch { return; /* no outcomes yet */ }
        const entries = parseRealizedLog(text);
        if (!entries.length) return;
        const KEY = 'codoc.realizedSeen';
        const prior = this.context.workspaceState.get<string[]>(KEY);
        const seen = new Set(prior ?? []);
        const fresh = newOutcomes(entries, seen);
        if (prior !== undefined && fresh.length) this._onDidRealize.fire(fresh);
        if (fresh.length || prior === undefined) {
            for (const e of entries) seen.add(e.id);
            // Bounded: the log itself is trimmed to a tail; keep a superset window.
            void this.context.workspaceState.update(KEY, [...seen].slice(-400));
        }
    }

    private _clearLeaseTimer(): void {
        if (this._leaseTimer !== undefined) {
            clearTimeout(this._leaseTimer);
            this._leaseTimer = undefined;
        }
    }

    /**
     * Arm a one-shot timer to re-derive state the instant a TTL lease expires
     * (review #10). The realizing lease and the agent-epoch lease both decay
     * purely by the passage of time — a crashed /codoc:sync or a killed session
     * writes no closing event, so nothing in the file watchers would ever fire to
     * repaint the status bar / webview off "implementing…" / "agent working…".
     * We schedule a single reload at the earliest pending expiry; the reload
     * re-reads from disk (the lease is now past → decays) and re-arms only if some
     * OTHER lease is still live, so this self-terminates rather than polling.
     */
    private _scheduleLeaseExpiry(nowMs: number = Date.now()): void {
        this._clearLeaseTimer();
        const expiries: number[] = [];
        // A still-fresh `realizing` (survived the read-time lease decay) will go
        // stale at this instant with no file event to announce it.
        if (this._status.state === 'realizing' && this._statusMtimeMs !== undefined) {
            expiries.push(this._statusMtimeMs + REALIZING_LEASE_MS);
        }
        // A still-live agent epoch (the "agent working…" bar) decays the same way.
        if (this.agentActive && this._activityMtimeMs !== undefined) {
            expiries.push(this._activityMtimeMs + EPOCH_UI_TTL_MS);
        }
        // A pending host log flips the pill to "not running" when the grace
        // passes — an expiry with no file event, exactly like the leases above.
        if (this._hostLogBytes > 0 && this._hostLogMtimeMs !== undefined
            && !daemonUnresponsive(this._hostLogBytes, this._hostLogMtimeMs, nowMs)) {
            expiries.push(this._hostLogMtimeMs + HOST_LOG_GRACE_MS);
        }
        const next = expiries.filter(t => t > nowMs).sort((a, b) => a - b)[0];
        if (next === undefined) return;
        // +50ms cushion so the lease clock is definitively past when we re-read.
        this._leaseTimer = setTimeout(() => {
            this._leaseTimer = undefined;
            this._reload();
        }, (next - nowMs) + 50);
    }

    /** Reflect whether one-click setup is actively provisioning (drives the
     *  "$(cloud-download) setting up…" status-bar state). Called by extension.ts
     *  around the setup flow. */
    setProvisioning(active: boolean): void {
        this._provisioning = active;
        this._updateStatusBar();
    }

    private _updateStatusBar(): void {
        const bar = this.statusBar;
        const view = statusBarView({
            initialized: this._rootDir !== null,
            provisioning: this._provisioning,
            agentActive: this.agentActive,
            daemonDown: this._statusNeverWritten
                || daemonUnresponsive(this._hostLogBytes, this._hostLogMtimeMs),
            agentFileCount: Object.keys(this._activity.touched ?? {}).length,
            state: this._status.state,
            pending: this._status.pending,
            detail: this._status.detail,
            featureCount: this._features.filter(f => !f.retired).length,
        });
        bar.text = view.text;
        bar.tooltip = view.tooltip;
        bar.command = view.command;
        // The warning background is reserved for the ONE "you owe an action" state.
        bar.backgroundColor = view.warn
            ? new vscode.ThemeColor('statusBarItem.warningBackground')
            : undefined;
        bar.show();
    }

    /** Record an Accept/Reject verdict by APPENDING to .codoc/inbox.host.jsonl —
     *  one JSON line per click; the daemon folds the log into inbox.json under the
     *  inbox lock (inbox.merge_host_verdicts) and applies it.
     *
     *  Appending is the whole point: this host holds no cross-process lock, so the
     *  old read-modify-write of inbox.json could land inside the daemon's locked
     *  drop_verdicts window and erase a verdict it was about to write back — a
     *  click silently lost, indistinguishable from never clicking. An append can't
     *  erase anything, and the merge dedups by event id (last line wins), which
     *  keeps the double-click behaviour the old writer had. Same pattern as
     *  edits.host.jsonl → edits.json. */
    writeVerdict(
        eventIds: string[], accept: boolean,
        edits?: { title?: string; description?: string },
    ): void {
        if (!this._rootDir || eventIds.length === 0) return;
        // Accept-time edits (an editable ghost amended before acceptance) ride the
        // verdict line only when present and only on an accept — a reject discards
        // the proposal, edits and all, and a plain verdict's line shape is unchanged.
        const extra = accept && edits
            ? {
                ...(edits.title?.trim() ? { title: edits.title } : {}),
                ...(edits.description?.trim() ? { description: edits.description } : {}),
            }
            : {};
        const lines = eventIds.map(id =>
            JSON.stringify({ event_id: id, accept, ...extra }) + '\n').join('');
        fs.appendFileSync(this._codocPath('inbox.host.jsonl'), lines);
    }

    get rootDir(): string | null { return this._rootDir; }
    get features(): ParsedFeature[] { return this._features; }
    get proposals(): ProposalHunk[] { return this._proposals; }
    get sidecar(): SidecarData { return this._sidecar; }
    get registry(): RegistryData | null { return this._registry; }
    get status(): CodocStatus { return this._status; }
    get activity(): ActivityData { return this._activity; }
    /** `codoc translate` progress (lease-guarded), or null when no run is in play. */
    get translation(): TranslationProgress | null { return this._translation; }
    get ask(): AskWalkthrough | null { return this._ask; }
    /** activity.json's last-modified time — the epoch lease's `last_seen`. */
    get activityMtimeMs(): number | undefined { return this._activityMtimeMs; }
    get agentActive(): boolean { return isAgentActive(this._activity, this._activityMtimeMs); }
    /** Code the queued tree edits (.codoc/realize.md) will touch, by repo-relative file. */
    get pendingCode(): Map<string, PendingChange[]> { return this._pendingCode; }
    pendingCodeForFile(relPath: string): PendingChange[] { return this._pendingCode.get(relPath) ?? []; }
    get activeFeatureLines(): number[] {
        return computeActiveFeatureLines(this._activity, this._features, this._sidecar, this._activityMtimeMs);
    }
    get featureEdges(): Map<string, Set<string>> {
        return featureAdjacency(this._sidecar);
    }
}
