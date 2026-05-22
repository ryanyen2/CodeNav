import * as vscode from 'vscode';
import * as path from 'path';
import { ServerState } from '../state/server';

const BINDINGS_RE = /^\s*(?:bindings|candidate-bindings):\s*(.*)$/;

export class CodocDefinitionProvider implements vscode.DefinitionProvider {
    constructor(private server: ServerState) {}

    async provideDefinition(
        document: vscode.TextDocument,
        position: vscode.Position,
    ): Promise<vscode.Location | null> {
        const lineText = document.lineAt(position.line).text;
        const m = BINDINGS_RE.exec(lineText);
        if (!m) return null;
        const list = m[1];

        // Each entry is "file:symbol_path". Find the entry the cursor sits on.
        const entries = list.split(',').map(s => s.trim());
        let cursorChar = position.character - lineText.indexOf(list);
        if (cursorChar < 0) cursorChar = 0;
        let acc = 0;
        let chosen = entries[0] ?? '';
        for (const e of entries) {
            const len = e.length + 2; // ", "
            if (cursorChar < acc + len) { chosen = e; break; }
            acc += len;
        }
        if (!chosen) return null;

        const colonIdx = chosen.indexOf(':');
        if (colonIdx < 0) return null;
        const file = chosen.slice(0, colonIdx).trim();
        const symbol = chosen.slice(colonIdx + 1).trim();

        const rootDir = this.server.rootDir;
        if (!rootDir) return null;

        // Try server-side resolve to get a precise line range.
        if (this.server.connected && this.server.client) {
            try {
                const pos = await this.server.client.resolveAnchor(file, symbol || null, null, 0);
                if (pos) {
                    const fileUri = vscode.Uri.file(path.join(rootDir, file));
                    return new vscode.Location(
                        fileUri,
                        new vscode.Range(pos.start_line, 0, pos.end_line, 0),
                    );
                }
            } catch {
                // Fall through to plain file open.
            }
        }

        const fileUri = vscode.Uri.file(path.join(rootDir, file));
        return new vscode.Location(fileUri, new vscode.Position(0, 0));
    }
}
