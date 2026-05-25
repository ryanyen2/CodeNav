import * as vscode from 'vscode';

// indent + marker + full title (everything up to an optional (state) suffix or
// trailing legacy [Badge]/# @uuid). Captures the WHOLE title, not just the first word.
const FEATURE_RE = /^(\s*)([-~*])\s+(.+?)(?:\s+\((?:strained|severed|stub|deprecated)\))?(?:\s+\[([^\]]*)\])?\s*(?:#\s*@([0-9a-f-]+))?\s*$/i;
const DIFF_HUNK_RE = /^[+\-~] [-~ ]/;

interface FeatureLine {
    line: number;
    indent: number;
    slug: string;
    badge: string | null;
    range: vscode.Range;
}

export class CodocSymbolProvider implements vscode.DocumentSymbolProvider {
    provideDocumentSymbols(document: vscode.TextDocument): vscode.DocumentSymbol[] {
        const flat: FeatureLine[] = [];
        for (let i = 0; i < document.lineCount; i++) {
            const text = document.lineAt(i).text;
            if (DIFF_HUNK_RE.test(text)) continue; // skip proposal diff hunks
            const m = FEATURE_RE.exec(text);
            if (!m) continue;
            const indent = m[1].length;
            const slug = m[3].replace(/\s*⟨[^⟩]*⟩\s*$/, '').trim();
            const badge = m[4] ?? null;
            flat.push({
                line: i,
                indent,
                slug,
                badge,
                range: new vscode.Range(i, 0, i, text.length),
            });
        }

        // Build hierarchy from indent stack.
        const roots: vscode.DocumentSymbol[] = [];
        const stack: Array<{ symbol: vscode.DocumentSymbol; indent: number }> = [];
        for (const f of flat) {
            const detail = f.badge ?? '';
            const sym = new vscode.DocumentSymbol(
                f.slug,
                detail,
                vscode.SymbolKind.Module,
                f.range,
                f.range,
            );
            while (stack.length > 0 && stack[stack.length - 1].indent >= f.indent) {
                stack.pop();
            }
            if (stack.length === 0) {
                roots.push(sym);
            } else {
                stack[stack.length - 1].symbol.children.push(sym);
            }
            stack.push({ symbol: sym, indent: f.indent });
        }
        return roots;
    }
}
