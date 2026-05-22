import * as vscode from 'vscode';

export interface ActivityEvent {
    session_id: string;
    phase: 'pre' | 'post';
    tool: string;
    rel_path: string;
    feature_uuids: string[];
    feature_slugs: string[];
}

export interface ActiveEntry {
    tool: string;
    features: string[];  // slugs
    expiresAt: number;   // Date.now() + 30_000
}

export class LiveActivityTracker {
    private _map = new Map<string, ActiveEntry>();  // rel_path → entry
    private _decorationType: vscode.TextEditorDecorationType;
    private _statusBar: vscode.StatusBarItem;
    private _timer: ReturnType<typeof setInterval> | null = null;

    constructor(context: vscode.ExtensionContext) {
        this._decorationType = vscode.window.createTextEditorDecorationType({
            gutterIconPath: context.asAbsolutePath('media/gutter-claude-active.svg'),
            gutterIconSize: '60%',
            overviewRulerColor: new vscode.ThemeColor('charts.blue'),
            overviewRulerLane: vscode.OverviewRulerLane.Left,
        });
        context.subscriptions.push(this._decorationType);

        this._statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 199);
        context.subscriptions.push(this._statusBar);

        // Evict expired entries every 5s.
        this._timer = setInterval(() => this._evictExpired(), 5_000);
        context.subscriptions.push({ dispose: () => { if (this._timer) clearInterval(this._timer); } });
    }

    handleEvent(raw: unknown): void {
        const event = raw as ActivityEvent;
        if (!event?.rel_path) return;
        this._map.set(event.rel_path, {
            tool: event.tool,
            features: event.feature_slugs ?? [],
            expiresAt: Date.now() + 30_000,
        });
        this._refresh();
    }

    private _evictExpired(): void {
        const now = Date.now();
        for (const [k, v] of this._map) {
            if (v.expiresAt <= now) this._map.delete(k);
        }
        this._refresh();
    }

    private _refresh(): void {
        // Update gutter decorations on open editors.
        for (const editor of vscode.window.visibleTextEditors) {
            const ws = vscode.workspace.getWorkspaceFolder(editor.document.uri);
            if (!ws) continue;
            const rel = vscode.workspace.asRelativePath(editor.document.uri, false);
            if (this._map.has(rel)) {
                // Decorate entire first line as a signal.
                const range = new vscode.Range(0, 0, 0, 0);
                editor.setDecorations(this._decorationType, [range]);
            } else {
                editor.setDecorations(this._decorationType, []);
            }
        }

        // Update status bar.
        const entries = [...this._map.entries()];
        if (entries.length === 0) {
            this._statusBar.hide();
            return;
        }
        const [relPath, entry] = entries[entries.length - 1];
        const filename = relPath.split('/').pop() ?? relPath;
        const featureHint = entry.features.length > 0 ? ` [${entry.features[0]}]` : '';
        this._statusBar.text = `$(loading~spin) Claude: ${entry.tool.split(':').pop()} ${filename}${featureHint}`;
        this._statusBar.tooltip = `Claude Code is working on ${relPath}`;
        this._statusBar.show();
    }
}
