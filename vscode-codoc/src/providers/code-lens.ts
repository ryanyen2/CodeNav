import * as vscode from 'vscode';
import { WorkspaceState } from '../state/workspace-state';
import { entriesForFile } from '../state/bindings-model';

export class CodocCodeLensProvider implements vscode.CodeLensProvider {
    constructor(private state: WorkspaceState) {}

    provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
        if (!this.state.rootDir) return [];

        const relPath = vscode.workspace.asRelativePath(document.fileName);
        const entries = entriesForFile(this.state.sidecar, relPath);

        // Build a symbol → feature map for fast lookup by symbol name.
        // Symbol path format: "file.py::ClassName.method_name" — the leaf is
        // what we match against declaration lines.
        const byLeaf = new Map<string, { title: string; id: string }>();
        for (const e of entries) {
            const leaf = e.symbol.includes('::') ? e.symbol.split('::')[1] : e.symbol;
            byLeaf.set(leaf, { title: e.feature_title, id: e.feature_id });
        }

        // Reverse direction: symbols a queued tree edit will rework (only while
        // the pipeline is awaiting implementation), keyed by leaf name.
        const willChange = this._pendingByLeaf(relPath);

        const lenses: vscode.CodeLens[] = [];
        for (let i = 0; i < document.lineCount; i++) {
            const line = document.lineAt(i).text;
            const isDecl = /^\s*(def |class |function |async def |export\s+(function|class|default))/.test(line);
            if (!isDecl) continue;

            const declName = (/(?:def |class |function |async def )\s*(\w+)/.exec(line) ?? [])[1];
            const range = new vscode.Range(i, 0, i, line.length);

            const entry = declName ? byLeaf.get(declName) : undefined;
            if (entry) {
                lenses.push(new vscode.CodeLens(range, {
                    title: `codoc: ${entry.title}`,
                    command: 'codoc.navigateToFeature',
                    arguments: [entry.id],
                    tooltip: 'Reveal this feature in the codoc tree',
                }));
            }

            const pending = declName ? willChange.get(declName) : undefined;
            if (pending) {
                lenses.push(new vscode.CodeLens(range, {
                    title: `codoc: ⟳ will change · ${pending.title}`,
                    command: 'codoc.open',
                    tooltip: `A queued tree edit ("${pending.title}") will rework this — run /codoc:realize to implement it`,
                }));
            }
        }
        return lenses;
    }

    /** Leaf symbol name → the queued change touching it (awaiting_impl/tree_dirty only). */
    private _pendingByLeaf(relPath: string): Map<string, { title: string }> {
        const map = new Map<string, { title: string }>();
        const state = this.state.status.state;
        if (state !== 'awaiting_impl' && state !== 'tree_dirty') return map;
        for (const change of this.state.pendingCodeForFile(relPath)) {
            if (!change.symbol) continue;
            const leaf = change.symbol.includes('::') ? change.symbol.split('::').pop()! : change.symbol;
            map.set(leaf, { title: change.title });
        }
        return map;
    }
}
