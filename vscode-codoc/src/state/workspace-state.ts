/**
 * WorkspaceState — file-based replacement for the old ServerState / HTTP client.
 *
 * Watches .codoc/tree.codoc and .codoc/tree.bindings.json; parses them on any
 * change; fires onDidChange so providers can refresh without polling.
 *
 * Status bar rules (never "offline"):
 *   $(sync) codoc: not initialized   – no .codoc dir
 *   $(bell) codoc: N proposals       – pending proposals (warning colour)
 *   $(check) codoc: N features       – healthy
 */

import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { parseTreeCodoc, ParsedFeature } from './tree-model';
import { SidecarData, emptySidecar } from './bindings-model';

export { ParsedFeature, SidecarData };

export class WorkspaceState {
    readonly statusBar: vscode.StatusBarItem;
    private _rootDir: string | null = null;
    private _features: ParsedFeature[] = [];
    private _sidecar: SidecarData = emptySidecar();
    private _pendingCount = 0;

    private _onDidChange = new vscode.EventEmitter<void>();
    readonly onDidChange = this._onDidChange.event;

    /** Compatibility shim: providers that check server.client return [] gracefully. */
    get client(): null { return null; }
    /** Compatibility shim: always true (we're file-based, never "offline"). */
    get connected(): boolean { return this._rootDir !== null; }

    constructor(private context: vscode.ExtensionContext) {
        this.statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
        this.statusBar.command = 'codoc.open';
        context.subscriptions.push(this.statusBar);
        this._init();
    }

    private _init(): void {
        this._rootDir = this.detectRootDir();
        this._reload();

        const treeWatcher = vscode.workspace.createFileSystemWatcher('**/.codoc/tree.codoc');
        const sidecarWatcher = vscode.workspace.createFileSystemWatcher('**/.codoc/tree.bindings.json');

        const reload = (): void => {
            this._rootDir = this.detectRootDir();
            this._reload();
        };

        this.context.subscriptions.push(
            treeWatcher,
            treeWatcher.onDidChange(reload),
            treeWatcher.onDidCreate(reload),
            treeWatcher.onDidDelete(reload),
            sidecarWatcher,
            sidecarWatcher.onDidChange(reload),
            sidecarWatcher.onDidCreate(reload),
        );
    }

    detectRootDir(): string | null {
        const cfg = vscode.workspace.getConfiguration('codoc');
        const manual: string = cfg.get('rootDir', '');
        if (manual && fs.existsSync(path.join(manual, '.codoc'))) return manual;

        for (const folder of vscode.workspace.workspaceFolders ?? []) {
            const candidate = path.join(folder.uri.fsPath, '.codoc');
            if (fs.existsSync(candidate)) return folder.uri.fsPath;
        }
        return null;
    }

    private _reload(): void {
        if (!this._rootDir) {
            this._features = [];
            this._sidecar = emptySidecar();
            this._pendingCount = 0;
            this._updateStatusBar();
            this._onDidChange.fire();
            return;
        }

        const treePath = path.join(this._rootDir, '.codoc', 'tree.codoc');
        const sidecarPath = path.join(this._rootDir, '.codoc', 'tree.bindings.json');

        try {
            const text = fs.readFileSync(treePath, 'utf-8');
            const parsed = parseTreeCodoc(text);
            this._features = parsed.features;
            this._pendingCount = parsed.pendingCount;
        } catch {
            this._features = [];
            this._pendingCount = 0;
        }

        try {
            this._sidecar = JSON.parse(fs.readFileSync(sidecarPath, 'utf-8')) as SidecarData;
        } catch {
            this._sidecar = emptySidecar();
        }

        this._updateStatusBar();
        this._onDidChange.fire();
    }

    private _updateStatusBar(): void {
        if (!this._rootDir) {
            this.statusBar.text = '$(sync) codoc: not initialized';
            this.statusBar.tooltip = 'No .codoc directory — run `codoc init` to initialize';
            this.statusBar.backgroundColor = undefined;
        } else if (this._pendingCount > 0) {
            this.statusBar.text = `$(bell) codoc: ${this._pendingCount} proposal${this._pendingCount === 1 ? '' : 's'}`;
            this.statusBar.tooltip = `${this._pendingCount} pending proposal(s) — edit tree.codoc and change '?' → '+' to accept or '-' to reject`;
            this.statusBar.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
        } else {
            const count = this._features.filter(f => !f.retired).length;
            this.statusBar.text = `$(check) codoc: ${count}`;
            this.statusBar.tooltip = `codoc: ${count} feature${count === 1 ? '' : 's'} — tree in sync`;
            this.statusBar.backgroundColor = undefined;
        }
        this.statusBar.show();
    }

    /** Force a reload (e.g., after a sync command). */
    async refreshState(): Promise<void> {
        this._rootDir = this.detectRootDir();
        this._reload();
    }

    get rootDir(): string | null { return this._rootDir; }
    get features(): ParsedFeature[] { return this._features; }
    get sidecar(): SidecarData { return this._sidecar; }
    get pendingCount(): number { return this._pendingCount; }
}
