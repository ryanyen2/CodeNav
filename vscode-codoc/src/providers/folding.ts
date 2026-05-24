import * as vscode from 'vscode';
import { collectFeatureLines } from './feature-lines';

// Attribute-only folding for .codoc files.
//
// Each feature folds ONLY its own attribute block (purpose / rationale /
// scenario / needs) — i.e. from its title line down to the line just before
// the NEXT feature title (at any depth). Child titles are never swallowed by
// a parent's fold, so collapsing a feature keeps the full title outline visible.
//
// Consequence: "Fold All" (Cmd+K Cmd+0) collapses every attribute block and
// leaves a clean table-of-contents of all titles at every level. To fold a
// whole subtree's attributes at once, use codoc.collapseFeatureSubtree.
export class CodocFoldingProvider implements vscode.FoldingRangeProvider {
    provideFoldingRanges(document: vscode.TextDocument): vscode.FoldingRange[] {
        const titles = collectFeatureLines(document);
        const ranges: vscode.FoldingRange[] = [];

        for (let k = 0; k < titles.length; k++) {
            const start = titles[k].line;
            const nextTitle = k + 1 < titles.length ? titles[k + 1].line : document.lineCount;

            // Attribute block = (start, nextTitle); trim trailing blank lines so
            // the blank separator before the next title stays visible when folded.
            let end = nextTitle - 1;
            while (end > start && document.lineAt(end).text.trim() === '') end--;

            if (end > start) {
                ranges.push(new vscode.FoldingRange(start, end, vscode.FoldingRangeKind.Region));
            }
        }

        return ranges;
    }
}
