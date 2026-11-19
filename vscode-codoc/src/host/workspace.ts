/**
 * workspace.ts: which folder holds `.codoc`, and what the daemon says about it.
 *
 * The extension host keeps no model of the tree. It needs the repository root,
 * whether that root has been initialised, whether a daemon is running, and the
 * daemon's `status.json` for the status bar. `Workspace` watches the files
 * those answers come from and fires `onDidChange` when any of them changes.
 *
 * One root per window: the first workspace folder that contains `.codoc`, or
 * the first folder when none does.
 */
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as vscode from 'vscode';

import { readPid } from './pidfile';

interface Status { state?: string; pending?: number; detail?: string; features?: number }

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

    /** The op log exists, so `codoc init` has run here. */
    get initialized(): boolean {
        return !!this.codocDir && fs.existsSync(path.join(this.codocDir, 'log'));
    }

    get daemonRunning(): boolean {
        const pid = this.codocDir ? readPid(this.codocDir) : undefined;
        if (pid === undefined) return false;
        try {
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

/** The status bar item: one line saying what the person owes, and the command a click runs. */
export function statusBarView(ws: Workspace, hasExecs: boolean): { text: string; tooltip: string; command: string | undefined; warn: boolean } {
    if (ws.provisioning) return { text: '$(cloud-download) codoc: setting up…', tooltip: 'installing and indexing', command: undefined, warn: false };
    if (!ws.initialized || !hasExecs) return { text: '$(sparkle) codoc: set up', tooltip: 'index this repo and build its feature tree', command: 'codoc.setup', warn: false };
    if (!ws.daemonRunning) return { text: '$(debug-disconnect) codoc: daemon off', tooltip: 'edits wait until the daemon runs', command: 'codoc.startDaemon', warn: true };
    const st = ws.status;
    const n = st.features ?? 0;
    switch (st.state) {
        case 'queued': return { text: `$(rocket) codoc: ${st.pending ?? 0} to build`, tooltip: st.detail ?? 'handed off; run /codoc:realize in Claude Code, or Build now', command: 'codoc.realize', warn: true };
        case 'edited': return { text: `$(edit) codoc: ${st.pending ?? 0} edited`, tooltip: st.detail ?? 'hand off with ⌘S in the tree', command: 'codoc.open', warn: false };
        case 'proposed': return { text: `$(comment-discussion) codoc: ${st.pending ?? 0} proposed`, tooltip: st.detail ?? 'plans await your verdict', command: 'codoc.open', warn: false };
        case 'realizing': return { text: '$(sync~spin) codoc: building', tooltip: st.detail ?? 'an agent is building', command: 'codoc.open', warn: false };
        default: return { text: `$(book) codoc: ${n} features`, tooltip: 'in step with the code', command: 'codoc.open', warn: false };
    }
}
