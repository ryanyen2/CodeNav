import * as vscode from 'vscode';
import { parseTreeLine } from 'codenav-semantic-tree/extension-api';

export function registerDocumentSymbolProvider(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.languages.registerDocumentSymbolProvider(
      { language: 'codoc' },
      {
        provideDocumentSymbols(document) {
          const symbols: vscode.DocumentSymbol[] = [];
          const stack: { sym: vscode.DocumentSymbol; depth: number }[] = [];
          for (let i = 0; i < document.lineCount; i++) {
            const line = document.lineAt(i);
            const parsed = parseTreeLine(line.text);
            if (!parsed) continue;
            const name = parsed.metadata?.entity_name || parsed.feature || parsed.sigil;
            const range = new vscode.Range(i, 0, i, line.text.length);
            const sym = new vscode.DocumentSymbol(
              name,
              `${parsed.sigil} ${parsed.feature}`,
              vscode.SymbolKind.Field,
              range,
              range
            );
            while (stack.length > 0 && stack[stack.length - 1].depth >= parsed.depth) stack.pop();
            if (stack.length > 0) stack[stack.length - 1].sym.children.push(sym);
            else symbols.push(sym);
            stack.push({ sym, depth: parsed.depth });
          }
          return symbols;
        },
      }
    )
  );
}
