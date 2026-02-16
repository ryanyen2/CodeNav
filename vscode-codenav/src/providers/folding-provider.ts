import * as vscode from 'vscode';
import { parseTreeLine } from 'codenav-semantic-tree/extension-api';

export function registerFoldingProvider(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.languages.registerFoldingRangeProvider(
      { language: 'codoc' },
      {
        provideFoldingRanges(document) {
          const ranges: vscode.FoldingRange[] = [];
          const stack: { depth: number; start: number }[] = [];
          for (let i = 0; i < document.lineCount; i++) {
            const line = document.lineAt(i);
            const parsed = parseTreeLine(line.text);
            if (!parsed) continue;
            while (stack.length > 0 && stack[stack.length - 1].depth >= parsed.depth) {
              const pop = stack.pop()!;
              if (i - 1 > pop.start) {
                ranges.push(new vscode.FoldingRange(pop.start, i - 1));
              }
            }
            stack.push({ depth: parsed.depth, start: i });
          }
          while (stack.length > 0) {
            const pop = stack.pop()!;
            if (document.lineCount - 1 > pop.start) {
              ranges.push(new vscode.FoldingRange(pop.start, document.lineCount - 1));
            }
          }
          return ranges;
        },
      }
    )
  );
}
