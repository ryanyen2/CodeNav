import * as vscode from 'vscode';
import { ServerState } from '../state/server';

export class CodocCodeLensProvider implements vscode.CodeLensProvider {
    constructor(private server: ServerState) {}

    async provideCodeLenses(document: vscode.TextDocument): Promise<vscode.CodeLens[]> {
        if (!this.server.connected || !this.server.client) return [];

        const relPath = vscode.workspace.asRelativePath(document.fileName);
        let bindingMap: Record<string, string> = {};
        try {
            bindingMap = await this.server.client.getBindingsByFile(relPath);
        } catch {
            // Server unreachable or endpoint not yet implemented — fall back to generic lens.
        }

        const lenses: vscode.CodeLens[] = [];
        for (let i = 0; i < document.lineCount; i++) {
            const line = document.lineAt(i).text;
            const isDecl = /^\s*(def |class |function |async def |export\s+(function|class|default))/.test(line);
            if (!isDecl) continue;

            const range = new vscode.Range(i, 0, i, line.length);
            const featureTitle = findFeatureTitleForLine(bindingMap, i);

            if (featureTitle) {
                lenses.push(new vscode.CodeLens(range, {
                    title: `codoc: ${featureTitle}`,
                    command: 'codoc.navigateToFeature',
                    arguments: [featureTitle],
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
 * Given a map of {symbolOrRange → featureTitle} returned by the server's
 * /bindings/by-file endpoint, find the best match for a given line number.
 * The server may return line numbers as keys (e.g. "42") or symbol names.
 * We try line-number first, then fall back to returning null.
 */
function findFeatureTitleForLine(bindingMap: Record<string, string>, line: number): string | null {
    // Key could be a stringified line number
    const byLine = bindingMap[String(line)];
    if (byLine) return byLine;
    return null;
}
