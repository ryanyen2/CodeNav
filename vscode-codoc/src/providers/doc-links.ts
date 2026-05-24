import * as vscode from 'vscode';
import * as path from 'path';
import { ServerState } from '../state/server';

// Matches [ref: file.py::symbol] or [ref:file.py::symbol] (with or without space after colon)
const REF_RE = /\[ref:\s*([^\]]+)\]/g;

export class CodocDocumentLinkProvider implements vscode.DocumentLinkProvider {
    constructor(private server: ServerState) {}

    provideDocumentLinks(document: vscode.TextDocument): vscode.DocumentLink[] {
        const rootDir = this.server.rootDir;
        if (!rootDir) return [];

        const links: vscode.DocumentLink[] = [];

        for (let i = 0; i < document.lineCount; i++) {
            const text = document.lineAt(i).text;
            REF_RE.lastIndex = 0;
            let m: RegExpExecArray | null;
            while ((m = REF_RE.exec(text)) !== null) {
                const refText = m[1].trim(); // e.g. "ingest/content_hash.py::store_blob"
                const sepIdx = refText.indexOf('::');
                const file = sepIdx >= 0 ? refText.slice(0, sepIdx) : refText;
                if (!file) continue;

                const start = new vscode.Position(i, m.index);
                const end = new vscode.Position(i, m.index + m[0].length);
                const range = new vscode.Range(start, end);

                const fileUri = vscode.Uri.file(path.join(rootDir, file));
                const link = new vscode.DocumentLink(range, fileUri);
                link.tooltip = `Open ${refText}`;
                links.push(link);
            }
        }

        return links;
    }
}
