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
import { ActivityData, parseActivity, isAgentActive, computeActiveFeatureLines } from './activity-model';
import { parseRealize, pendingCodeByFile, PendingChange } from './realize-model';
import { statusBarView } from './status-presentation';

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
    private _pendingCode: Map<string, PendingChange[]> = new Map();
    private _provisioning = false;

    private _onDidChange = new vscode.EventEmitter<void>();
    readonly onDidChange = this._onDidChange.event;

    constructor(private context: vscode.ExtensionContext) {
        this.statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
        this.statusBar.command = 'codoc.open';
        context.subscriptions.push(this.statusBar);
        this._init();
    }

    private _init(): void {
        this._rootDir = this.detectRootDir();
        this._reload();

        const reload = (): void => { this._rootDir = this.detectRootDir(); this._reload(); };
        for (const glob of [
            '**/.codoc/tree.codoc',
            '**/.codoc/tree.doc.json',  // KTD9: daemon-written store projection — repaint the webview
            '**/.codoc/tree.bindings.json',
            '**/.codoc/tree.index.json',
            '**/.codoc/status.json',
            '**/.codoc/inbox.json',
            '**/.codoc/activity.json',
            '**/.codoc/realize.md',
        ]) {
            const w = vscode.workspace.createFileSystemWatcher(glob);
            this.context.subscriptions.push(w, w.onDidChange(reload), w.onDidCreate(reload), w.onDidDelete(reload));
        }
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

        try {
            const st = JSON.parse(fs.readFileSync(this._codocPath('status.json'), 'utf-8'));
            this._status = { state: st.state, pending: st.pending ?? 0, detail: st.detail ?? '' };
        } catch {
            // No status file yet → derive from the parsed proposal count.
            const n = this._proposals.length;
            this._status = { state: n ? 'code_drift' : 'in_sync', pending: n, detail: '' };
        }

        let activityText = '';
        this._activityMtimeMs = undefined;
        try {
            const activityPath = this._codocPath('activity.json');
            activityText = fs.readFileSync(activityPath, 'utf-8');
            this._activityMtimeMs = fs.statSync(activityPath).mtimeMs;
        } catch { /* file absent → no active agent */ }
        this._activity = parseActivity(activityText);

        // .codoc/realize.md → which code the queued tree edits will touch (reverse
        // direction: codoc → codebase placeholders). Absent when nothing queued.
        try {
            this._pendingCode = pendingCodeByFile(parseRealize(fs.readFileSync(this._codocPath('realize.md'), 'utf-8')));
        } catch {
            this._pendingCode = new Map();
        }

        this._updateStatusBar();
        this._onDidChange.fire();
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

    /** Append an Accept/Reject verdict to .codoc/inbox.json; the daemon applies it.
     *  Dedups by event id (last write wins) so a double-click — common when no daemon
     *  is draining and the card still shows — can't pile up duplicate verdicts. */
    writeVerdict(eventIds: string[], accept: boolean): void {
        if (!this._rootDir || eventIds.length === 0) return;
        const inboxPath = this._codocPath('inbox.json');
        const byEvent = new Map<string, boolean>();
        try {
            const existing: Array<{ event_id: string; accept: boolean }> = JSON.parse(fs.readFileSync(inboxPath, 'utf-8')).verdicts ?? [];
            for (const v of existing) byEvent.set(v.event_id, v.accept);
        } catch { /* no inbox yet */ }
        for (const id of eventIds) byEvent.set(id, accept);
        const verdicts = [...byEvent].map(([event_id, a]) => ({ event_id, accept: a }));
        fs.writeFileSync(inboxPath, JSON.stringify({ version: 1, verdicts }, null, 2));
    }

    get rootDir(): string | null { return this._rootDir; }
    get features(): ParsedFeature[] { return this._features; }
    get proposals(): ProposalHunk[] { return this._proposals; }
    get sidecar(): SidecarData { return this._sidecar; }
    get registry(): RegistryData | null { return this._registry; }
    get status(): CodocStatus { return this._status; }
    get activity(): ActivityData { return this._activity; }
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
