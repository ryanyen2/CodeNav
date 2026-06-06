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
import { ActivityData, parseActivity, isAgentActive, computeActiveFeatureLines } from './activity-model';
import { parseRealize, pendingCodeByFile, PendingChange } from './realize-model';

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
    private _status: CodocStatus = { state: 'in_sync', pending: 0, detail: '' };
    private _activity: ActivityData = {};
    private _pendingCode: Map<string, PendingChange[]> = new Map();

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
            '**/.codoc/tree.bindings.json',
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
        if (!this._rootDir) {
            this._features = [];
            this._proposals = [];
            this._sidecar = emptySidecar();
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

        try {
            const st = JSON.parse(fs.readFileSync(this._codocPath('status.json'), 'utf-8'));
            this._status = { state: st.state, pending: st.pending ?? 0, detail: st.detail ?? '' };
        } catch {
            // No status file yet → derive from the parsed proposal count.
            const n = this._proposals.length;
            this._status = { state: n ? 'code_drift' : 'in_sync', pending: n, detail: '' };
        }

        let activityText = '';
        try { activityText = fs.readFileSync(this._codocPath('activity.json'), 'utf-8'); } catch { /* file absent → no active agent */ }
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

    private _updateStatusBar(): void {
        const bar = this.statusBar;
        bar.backgroundColor = undefined;
        if (!this._rootDir) {
            bar.text = '$(sync) codoc: not initialized';
            bar.tooltip = 'No .codoc directory — run `codoc init` to initialize';
            bar.show();
            return;
        }
        if (this.agentActive) {
            const n = Object.keys(this._activity.touched ?? {}).length;
            bar.text = `$(zap) codoc: agent working… (${n} files)`;
            bar.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
            bar.show();
            return;
        }
        const { state, pending } = this._status;
        if (state === 'realizing') {
            bar.text = '$(loading~spin) codoc: implementing…';
            bar.tooltip = this._status.detail || 'The coding agent is implementing your tree edits';
        } else if (state === 'tree_dirty') {
            bar.text = '$(pencil) codoc: applying tree edits…';
            bar.tooltip = this._status.detail || 'tree.codoc was edited — realizing the code change';
        } else if (state === 'awaiting_impl') {
            bar.text = `$(play) codoc: ${pending} to implement`;
            bar.tooltip = this._status.detail
                || 'Accepted tree edits are queued in .codoc/realize.md — run /codoc:realize in your Claude Code session';
            bar.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
        } else if (state === 'code_drift' || pending > 0) {
            bar.text = `$(bell) codoc: ${pending} proposal${pending === 1 ? '' : 's'}`;
            bar.tooltip = 'Code changed — review proposed tree updates (Accept / Reject in the editor)';
            bar.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
        } else {
            const count = this._features.filter(f => !f.retired).length;
            bar.text = `$(check) codoc: ${count}`;
            bar.tooltip = `codoc: ${count} feature${count === 1 ? '' : 's'} — in sync`;
        }
        bar.show();
    }

    /** Append an Accept/Reject verdict to .codoc/inbox.json; the daemon applies it. */
    writeVerdict(eventIds: string[], accept: boolean): void {
        if (!this._rootDir || eventIds.length === 0) return;
        const inboxPath = this._codocPath('inbox.json');
        let verdicts: Array<{ event_id: string; accept: boolean }> = [];
        try {
            verdicts = JSON.parse(fs.readFileSync(inboxPath, 'utf-8')).verdicts ?? [];
        } catch { /* no inbox yet */ }
        for (const id of eventIds) verdicts.push({ event_id: id, accept });
        fs.writeFileSync(inboxPath, JSON.stringify({ version: 1, verdicts }, null, 2));
    }

    get rootDir(): string | null { return this._rootDir; }
    get features(): ParsedFeature[] { return this._features; }
    get proposals(): ProposalHunk[] { return this._proposals; }
    get sidecar(): SidecarData { return this._sidecar; }
    get status(): CodocStatus { return this._status; }
    get activity(): ActivityData { return this._activity; }
    get agentActive(): boolean { return isAgentActive(this._activity); }
    /** Code the queued tree edits (.codoc/realize.md) will touch, by repo-relative file. */
    get pendingCode(): Map<string, PendingChange[]> { return this._pendingCode; }
    pendingCodeForFile(relPath: string): PendingChange[] { return this._pendingCode.get(relPath) ?? []; }
    get activeFeatureLines(): number[] {
        return computeActiveFeatureLines(this._activity, this._features, this._sidecar);
    }
    get featureEdges(): Map<string, Set<string>> {
        return featureAdjacency(this._sidecar);
    }
}
