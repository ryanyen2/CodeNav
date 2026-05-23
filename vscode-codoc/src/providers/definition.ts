import * as vscode from 'vscode';
import * as path from 'path';
import { ServerState } from '../state/server';

// Matches binding reference lines:  -> code://file::symbol_path  or  -> file://path
const BINDING_REF_RE = /^\s*->\s*(?:code|file):\/\/(.+)$/;
// Matches @symbol or @file.py::Symbol inline refs (not preceded by a word char)
const AT_REF_RE = /(?<!\w)@([\w.]+(?:::[\w.]+)*)/g;

export class CodocDefinitionProvider implements vscode.DefinitionProvider {
    constructor(private server: ServerState) {}

    async provideDefinition(
        document: vscode.TextDocument,
        position: vscode.Position,
    ): Promise<vscode.Location | null> {
        const lineText = document.lineAt(position.line).text;
        const rootDir = this.server.rootDir;
        if (!rootDir) return null;

        // Check @symbol inline refs first
        AT_REF_RE.lastIndex = 0;
        let atMatch: RegExpExecArray | null;
        while ((atMatch = AT_REF_RE.exec(lineText)) !== null) {
            const matchStart = atMatch.index;
            const matchEnd = matchStart + atMatch[0].length;
            if (position.character >= matchStart && position.character <= matchEnd) {
                const ref = atMatch[1];
                const sepIdx = ref.indexOf('::');
                const file = sepIdx >= 0 ? ref.slice(0, sepIdx) : '';
                const symbol = sepIdx >= 0 ? ref.slice(sepIdx + 2) : ref;
                return this._resolveSymbol(rootDir, file, symbol);
            }
        }

        // Binding arrow lines:  -> code://file::symbol
        const m = BINDING_REF_RE.exec(lineText);
        if (!m) return null;
        const ref = m[1];
        const sepIdx = ref.indexOf('::');
        const file = sepIdx >= 0 ? ref.slice(0, sepIdx) : ref;
        const symbol = sepIdx >= 0 ? ref.slice(sepIdx + 2) : '';
        return this._resolveSymbol(rootDir, file, symbol || null);
    }

    private async _resolveSymbol(
        rootDir: string,
        file: string,
        symbol: string | null,
    ): Promise<vscode.Location | null> {
        if (this.server.connected && this.server.client) {
            try {
                const pos = await this.server.client.resolveAnchor(file, symbol, null, 0);
                if (pos && file) {
                    const fileUri = vscode.Uri.file(path.join(rootDir, file));
                    return new vscode.Location(fileUri, new vscode.Range(pos.start_line, 0, pos.end_line, 0));
                }
            } catch {
                // fall through
            }
        }
        if (file) {
            const fileUri = vscode.Uri.file(path.join(rootDir, file));
            return new vscode.Location(fileUri, new vscode.Position(0, 0));
        }
        return null;
    }
}
