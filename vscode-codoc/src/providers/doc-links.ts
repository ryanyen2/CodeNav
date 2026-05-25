import * as vscode from 'vscode';

/**
 * Make inline code citations clickable. A "[label](codoc:file.py#symbol)" link
 * navigates to the file and reveals the symbol (via the codoc.openRef command,
 * since file URIs can't carry a symbol target on their own).
 */
const REF_RE = /\[[^\]]*\]\(codoc:([^)#]+)(?:#([^)]+))?\)/g;

export class CodocDocumentLinkProvider implements vscode.DocumentLinkProvider {
    provideDocumentLinks(document: vscode.TextDocument): vscode.DocumentLink[] {
        if (document.languageId !== 'codoc') return [];
        const links: vscode.DocumentLink[] = [];

        for (let i = 0; i < document.lineCount; i++) {
            const text = document.lineAt(i).text;
            REF_RE.lastIndex = 0;
            let m: RegExpExecArray | null;
            while ((m = REF_RE.exec(text)) !== null) {
                const file = m[1];
                const symbol = m[2] ?? '';
                const range = new vscode.Range(i, m.index, i, m.index + m[0].length);
                const args = encodeURIComponent(JSON.stringify([file, symbol]));
                const link = new vscode.DocumentLink(range, vscode.Uri.parse(`command:codoc.openRef?${args}`));
                link.tooltip = symbol ? `Open ${file} › ${symbol}` : `Open ${file}`;
                links.push(link);
            }
        }
        return links;
    }
}
