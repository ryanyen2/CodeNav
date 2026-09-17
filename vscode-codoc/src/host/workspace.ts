/**
 * workspace.ts — where the `.codoc` directory is, and what `status.json` says.
 *
 * The host needs two facts about the workspace and no model of the tree: the
 * root that holds `.codoc/`, and the daemon's published status for the status
 * bar. Everything about the document lives in the webview, which reads the
 * projection the daemon publishes.
 */
import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';

export interface Status { state?: string; pending?: number; detail?: string; features?: number }

export class Workspace implements vscode.Disposable {
    readonly rootDir: string | null;
    status: Status = {};
    provisioning = false;
    private readonly emitter = new vscode.EventEmitter<void>();
    readonly onDidChange = this.emitter.event;
    private readonly subs: vscode.Disposable[] = [];

    constructor() {
        this.rootDir = detectRoot();
        if (this.rootDir) {
            const codoc = path.join(this.rootDir, '.codoc');
            const w = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(vscode.Uri.file(codoc), '{status.json,watch.pid,log/*.jsonl}'));
            this.subs.push(w, w.onDidChange(() => this.refresh()), w.onDidCreate(() => this.refresh()), w.onDidDelete(() => this.refresh()));
            this.refresh();
        }
    }

    get codocDir(): string | null {
        return this.rootDir ? path.join(this.rootDir, '.codoc') : null;
    }

    get initialized(): boolean {
        return !!this.codocDir && fs.existsSync(path.join(this.codocDir, 'log'));
    }

    get daemonRunning(): boolean {
        if (!this.codocDir) return false;
        try {
            const pid = Number(fs.readFileSync(path.join(this.codocDir, 'watch.pid'), 'utf8').trim());
            process.kill(pid, 0);
            return true;
        } catch {
            return false;
        }
    }

    refresh(): void {
        try {
            this.status = JSON.parse(fs.readFileSync(path.join(this.codocDir!, 'status.json'), 'utf8')) as Status;
        } catch {
            this.status = {};
        }
        this.emitter.fire();
    }

    setProvisioning(on: boolean): void {
        this.provisioning = on;
        this.emitter.fire();
    }

    dispose(): void {
        this.subs.forEach(s => s.dispose());
        this.emitter.dispose();
    }
}

function detectRoot(): string | null {
    const folders = vscode.workspace.workspaceFolders ?? [];
    for (const f of folders) {
        if (fs.existsSync(path.join(f.uri.fsPath, '.codoc'))) return f.uri.fsPath;
    }
    return folders[0]?.uri.fsPath ?? null;
}

/** The status bar's text and tooltip: one line, the thing the person owes. */
export function statusBarView(ws: Workspace, hasExecs: boolean): { text: string; tooltip: string; command: string | undefined; warn: boolean } {
    if (ws.provisioning) return { text: '$(cloud-download) codoc: setting up…', tooltip: 'installing and indexing', command: undefined, warn: false };
    if (!ws.initialized || !hasExecs) return { text: '$(sparkle) codoc: set up', tooltip: 'index this repo and build its feature tree', command: 'codoc.setup', warn: false };
    if (!ws.daemonRunning) return { text: '$(debug-disconnect) codoc: daemon off', tooltip: 'edits wait until the daemon runs', command: 'codoc.startDaemon', warn: true };
    const st = ws.status;
    const n = st.features ?? 0;
    switch (st.state) {
        case 'queued': return { text: `$(rocket) codoc: ${st.pending ?? 0} to build`, tooltip: st.detail ?? 'handed off; run /codoc:sync or Build now', command: 'codoc.realize', warn: true };
        case 'edited': return { text: `$(edit) codoc: ${st.pending ?? 0} edited`, tooltip: st.detail ?? 'hand off with ⌘S in the tree', command: 'codoc.open', warn: false };
        case 'proposed': return { text: `$(comment-discussion) codoc: ${st.pending ?? 0} proposed`, tooltip: st.detail ?? 'plans await your verdict', command: 'codoc.open', warn: false };
        case 'realizing': return { text: '$(sync~spin) codoc: building', tooltip: st.detail ?? 'an agent is implementing', command: 'codoc.open', warn: false };
        default: return { text: `$(book) codoc: ${n} features`, tooltip: 'in step with the code', command: 'codoc.open', warn: false };
    }
}
