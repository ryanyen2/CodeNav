import * as vscode from 'vscode';
import { WorkspaceState } from '../state/workspace-state';

/**
 * Autocomplete for inline code citations. Typing '[' in a description offers
 * every symbol codoc knows about and inserts a ready-made markdown link:
 *
 *     [where_to_bundle](codoc:certs.py#where_to_bundle)
 *
 * Symbols come from tree.bindings.json (by_file), so no server is needed.
 */
const OPEN_BRACKET_RE = /\[[^\]]*$/;        // just after a '[' (label being typed)
const CODOC_TARGET_RE = /\]\(codoc:[^)]*$/; // inside the (codoc:…) target

function leaf(symbol: string): string {
    const i = symbol.indexOf('::');
    return i >= 0 ? symbol.slice(i + 2) : symbol;
}

export class CodocCompletionProvider implements vscode.CompletionItemProvider {
    constructor(private state: WorkspaceState) {}

    provideCompletionItems(
        document: vscode.TextDocument,
        position: vscode.Position,
    ): vscode.CompletionItem[] {
        if (document.languageId !== 'codoc') return [];
        const prefix = document.lineAt(position.line).text.slice(0, position.character);

        const inTarget = CODOC_TARGET_RE.test(prefix);
        const afterBracket = OPEN_BRACKET_RE.test(prefix);
        if (!inTarget && !afterBracket) return [];

        const seen = new Set<string>();
        const items: vscode.CompletionItem[] = [];
        for (const [file, entries] of Object.entries(this.state.sidecar.by_file)) {
            for (const e of entries) {
                const name = leaf(e.symbol);
                const key = `${file}#${name}`;
                if (seen.has(key)) continue;
                seen.add(key);

                const item = new vscode.CompletionItem(name, vscode.CompletionItemKind.Reference);
                item.detail = `${file} · ${e.feature_title}`;
                item.documentation = new vscode.MarkdownString(`\`codoc:${file}#${name}\``);
                if (inTarget) {
                    // Completing the target: insert just "file#symbol".
                    item.insertText = `${file}#${name}`;
                    item.filterText = `${file}#${name}`;
                } else {
                    // Completing from '[': replace the bracket with a full link.
                    const start = prefix.lastIndexOf('[');
                    item.range = new vscode.Range(position.line, start, position.line, position.character);
                    item.insertText = new vscode.SnippetString(`[\${1:${name}}](codoc:${file}#${name})`);
                    item.filterText = `[${name}`;
                }
                items.push(item);
            }
        }
        return items;
    }
}
