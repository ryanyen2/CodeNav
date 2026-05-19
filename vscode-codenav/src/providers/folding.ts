import * as vscode from 'vscode';

// Indent-based folding for .codoc files. Each feature/proposal block folds
// under the line that introduces it. Indent unit = 2 spaces (writer convention).
export class CodocFoldingProvider implements vscode.FoldingRangeProvider {
    provideFoldingRanges(document: vscode.TextDocument): vscode.FoldingRange[] {
        const ranges: vscode.FoldingRange[] = [];
        const stack: Array<{ line: number; indent: number }> = [];

        const indentOf = (line: string): number => {
            let n = 0;
            while (n < line.length && line[n] === ' ') n++;
            return n;
        };

        const isHeader = (line: string): boolean => /^\s*[-~?!]\s/.test(line);

        for (let i = 0; i < document.lineCount; i++) {
            const text = document.lineAt(i).text;
            if (!text.trim()) continue;
            if (!isHeader(text)) continue;
            const indent = indentOf(text);

            while (stack.length > 0 && stack[stack.length - 1].indent >= indent) {
                const open = stack.pop()!;
                if (i - 1 > open.line) {
                    ranges.push(new vscode.FoldingRange(open.line, i - 1));
                }
            }
            stack.push({ line: i, indent });
        }

        const last = document.lineCount - 1;
        while (stack.length > 0) {
            const open = stack.pop()!;
            if (last > open.line) {
                ranges.push(new vscode.FoldingRange(open.line, last));
            }
        }

        return ranges;
    }
}
