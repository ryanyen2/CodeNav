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

            // Fold from the title line through everything up to (but not including)
            // the next title — including trailing blank separators. This keeps the
            // folded TOC view compact (no stray blank lines between titles).
            const end = nextTitle - 1;
            if (end > start) {
                ranges.push(new vscode.FoldingRange(start, end, vscode.FoldingRangeKind.Region));
            }
        }

        return ranges;
    }
}
