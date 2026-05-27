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

        const lenses: vscode.CodeLens[] = [];
        for (let i = 0; i < document.lineCount; i++) {
            const line = document.lineAt(i).text;
            const isDecl = /^\s*(def |class |function |async def |export\s+(function|class|default))/.test(line);
            if (!isDecl) continue;

            const entry = _findFeature(byLeaf, line);
            if (!entry) continue;

            const range = new vscode.Range(i, 0, i, line.length);
            lenses.push(new vscode.CodeLens(range, {
                title: `codoc: ${entry.title}`,
                command: 'codoc.navigateToFeature',
                arguments: [entry.id],
                tooltip: 'Reveal this feature in the codoc tree',
            }));
        }
        return lenses;
    }
}

/**
 * Identify the feature for a declaration line by matching the declared name
 * against the symbol leaf names we know from the sidecar.
 */
function _findFeature(byLeaf: Map<string, { title: string; id: string }>, line: string): { title: string; id: string } | null {
    // Extract the declared name from common patterns.
    const m = /(?:def |class |function |async def )\s*(\w+)/.exec(line);
    if (!m) return null;
    return byLeaf.get(m[1]) ?? null;
}
