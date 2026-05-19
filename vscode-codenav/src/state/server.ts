import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { CodocClient } from '../api/client';

export class ServerState {
    readonly statusBar: vscode.StatusBarItem;
    private _client: CodocClient | null = null;
    private _rootDir: string | null = null;
    private _connected = false;
    private _proposalCount: number | null = null;
    private _readyCallbacks: Array<() => void> = [];
    private _pollTimer: ReturnType<typeof setInterval> | null = null;

    constructor(private context: vscode.ExtensionContext) {
        this.statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
        this.statusBar.command = 'codoc.openIndex';
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
        this._pollTimer = setInterval(() => this.checkHealth(), 10_000);
        this.context.subscriptions.push({ dispose: () => { if (this._pollTimer) clearInterval(this._pollTimer); } });
    }

    private detectRootDir(): string | null {
        const cfg = vscode.workspace.getConfiguration('codoc');
        const manual: string = cfg.get('rootDir', '');
        if (manual) return manual;
        for (const folder of vscode.workspace.workspaceFolders ?? []) {
            const candidate = path.join(folder.uri.fsPath, '.codoc');
            if (fs.existsSync(candidate)) return folder.uri.fsPath;
        }
        return null;
    }

    private async checkHealth(): Promise<void> {
        if (!this._client) return;
        const ok = await this._client.health();
        const wasConnected = this._connected;
        this._connected = ok;
        if (ok && !wasConnected) {
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
        } else if (this._proposalCount !== null && this._proposalCount > 0) {
            this.statusBar.text = `$(bell) codoc: ${this._proposalCount}`;
            this.statusBar.tooltip = `${this._proposalCount} pending proposal${this._proposalCount === 1 ? '' : 's'} — click to review`;
            this.statusBar.backgroundColor = undefined;
        } else {
            this.statusBar.text = '$(check) codoc';
            this.statusBar.tooltip = `codoc: tree up to date — root: ${this._rootDir}`;
            this.statusBar.backgroundColor = undefined;
        }
        this.statusBar.show();
    }

    setProposalCount(n: number): void {
        this._proposalCount = n;
        if (this._connected) this._updateDisplay();
    }

    get client(): CodocClient | null { return this._client; }
    get rootDir(): string | null { return this._rootDir; }
    get connected(): boolean { return this._connected; }

    onReady(cb: () => void): void {
        if (this._connected) cb();
        else this._readyCallbacks.push(cb);
    }
}
