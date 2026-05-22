import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { CodocClient, StateResponse } from '../api/client';

export class ServerState {
    readonly statusBar: vscode.StatusBarItem;
    private _client: CodocClient | null = null;
    private _rootDir: string | null = null;
    private _connected = false;
    private _state: StateResponse | null = null;
    private _readyCallbacks: Array<() => void> = [];
    private _pollTimer: ReturnType<typeof setInterval> | null = null;
    private _cancelStream: (() => void) | null = null;
    private _activityCallbacks: Array<(data: unknown) => void> = [];

    constructor(private context: vscode.ExtensionContext) {
        this.statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
        this.statusBar.command = 'codoc.sync';
        context.subscriptions.push(this.statusBar);
        this.init();
    }

    private async init(): Promise<void> {
        this._rootDir = this.detectRootDir();
        if (!this._rootDir) {
            this._updateDisplay();
            return;
        }
        const cfg = vscode.workspace.getConfiguration('codoc');
        const baseUrl: string = cfg.get('serverUrl', 'http://localhost:8001');
        this._client = new CodocClient(baseUrl, this._rootDir);
        await this.checkHealth();
        this._startEventStream();
        this._pollTimer = setInterval(() => this.checkHealth(), 30_000);
        this.context.subscriptions.push({ dispose: () => {
            if (this._pollTimer) clearInterval(this._pollTimer);
            if (this._cancelStream) this._cancelStream();
        }});
    }

    detectRootDir(): string | null {
        const cfg = vscode.workspace.getConfiguration('codoc');
        const manual: string = cfg.get('rootDir', '');
        if (manual) return manual;
        for (const folder of vscode.workspace.workspaceFolders ?? []) {
            const candidate = path.join(folder.uri.fsPath, '.codoc');
            if (fs.existsSync(candidate)) return folder.uri.fsPath;
        }
        return null;
    }

    async checkHealth(): Promise<void> {
        if (!this._client) return;
        const state = await this._client.healthAndState();
        const wasConnected = this._connected;
        this._connected = state !== null;
        this._state = state;
        if (this._connected && !wasConnected) {
            this._readyCallbacks.forEach(cb => cb());
            this._readyCallbacks = [];
        }
        this._updateDisplay();
    }

    private _updateDisplay(): void {
        if (!this._rootDir) {
            this.statusBar.hide();
            return;
        }
        if (!this._connected) {
            this.statusBar.text = '$(warning) codoc: offline';
            this.statusBar.tooltip = 'codoc server not reachable — run `codoc server` in your terminal';
            this.statusBar.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
        } else if (this._state) {
            const { stage, pending_count, feature_count } = this._state;
            if (stage === 'proposals-pending' || stage === 'bootstrap-review') {
                this.statusBar.text = `$(bell) codoc: ${pending_count}`;
                this.statusBar.tooltip = `${pending_count} pending proposal${pending_count === 1 ? '' : 's'} — click to sync`;
                this.statusBar.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
            } else if (stage === 'needs-bootstrap' || stage === 'uninit') {
                this.statusBar.text = `$(sync) codoc: ${stage}`;
                this.statusBar.tooltip = this._state.next_action;
                this.statusBar.backgroundColor = undefined;
            } else if (stage === 'stale-render') {
                this.statusBar.text = '$(sync) codoc: stale';
                this.statusBar.tooltip = 'Tree render is behind — click to sync';
                this.statusBar.backgroundColor = undefined;
            } else {
                this.statusBar.text = `$(check) codoc: ${feature_count}`;
                this.statusBar.tooltip = `codoc: ${feature_count} features — tree in sync`;
                this.statusBar.backgroundColor = undefined;
            }
        }
        this.statusBar.show();
    }

    /** Call after any proposal accept/reject to refresh the status bar. */
    async refreshState(): Promise<void> {
        await this.checkHealth();
    }

    get client(): CodocClient | null { return this._client; }
    get rootDir(): string | null { return this._rootDir; }
    get connected(): boolean { return this._connected; }
    get repoState(): StateResponse | null { return this._state; }

    onReady(cb: () => void): void {
        if (this._connected) cb();
        else this._readyCallbacks.push(cb);
    }

    onActivity(cb: (data: unknown) => void): void {
        this._activityCallbacks.push(cb);
    }

    private _startEventStream(): void {
        if (!this._client || !this._rootDir) return;
        if (this._cancelStream) this._cancelStream();
        this._cancelStream = this._client.subscribeToEvents(
            (topic, data) => {
                if (topic === 'activity') {
                    this._activityCallbacks.forEach(cb => cb(data));
                } else if (topic === 'proposal' || topic === 'accept' || topic === 'reject' || topic === 'reflect_done') {
                    void this.checkHealth();
                }
            },
            () => { /* SSE error — polling fallback handles reconnect */ },
        );
    }
}
