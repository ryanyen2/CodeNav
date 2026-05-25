import * as vscode from 'vscode';
import { WorkspaceState } from '../state/workspace-state';
import { entriesForFile } from '../state/bindings-model';

export class CodocCodeLensProvider implements vscode.CodeLensProvider {
    constructor(private state: WorkspaceState) {}

    provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
        if (!this.state.rootDir) return [];

        const relPath = vscode.workspace.asRelativePath(document.fileName);
        const entries = entriesForFile(this.state.sidecar, relPath);

        // Build a symbol → feature_title map for fast lookup by symbol name.
        // Symbol path format: "file.py::ClassName.method_name" — the leaf is
        // what we match against declaration lines.
        const byLeaf = new Map<string, string>();
        for (const e of entries) {
            const leaf = e.symbol.includes('::') ? e.symbol.split('::')[1] : e.symbol;
            byLeaf.set(leaf, e.feature_title);
        }

        const lenses: vscode.CodeLens[] = [];
        for (let i = 0; i < document.lineCount; i++) {
            const line = document.lineAt(i).text;
            const isDecl = /^\s*(def |class |function |async def |export\s+(function|class|default))/.test(line);
            if (!isDecl) continue;

            const range = new vscode.Range(i, 0, i, line.length);
            const featureTitle = _findFeatureTitle(byLeaf, line);

            if (featureTitle) {
                lenses.push(new vscode.CodeLens(range, {
                    title: `codoc: ${featureTitle}`,
                    command: 'codoc.open',
                    tooltip: 'Open feature in codoc tree',
                }));
            } else {
                lenses.push(new vscode.CodeLens(range, {
                    title: 'codoc: unattributed',
                    command: 'codoc.open',
                    tooltip: 'Open codoc — this symbol is not attributed to a feature',
                }));
            }
        }
        return lenses;
    }
}

/**
 * Identify the feature title for a declaration line by matching the declared
 * name against the symbol leaf names we know from the sidecar.
 */
function _findFeatureTitle(byLeaf: Map<string, string>, line: string): string | null {
    // Extract the declared name from common patterns.
    const m = /(?:def |class |function |async def )\s*(\w+)/.exec(line);
    if (!m) return null;
    return byLeaf.get(m[1]) ?? null;
}
