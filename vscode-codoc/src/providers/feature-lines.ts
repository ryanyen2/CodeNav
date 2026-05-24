import * as vscode from 'vscode';

// A feature title line: indent + marker (- live, ~ retired, * placeholder) + space + title text.
// The first char after the marker must be a word/letter so we don't match col-0 diff
// hunks like "- ~ Retired" or "+ - New" (where position 2 is -, ~, or space).
const TITLE_RE = /^(\s*)[-~*]\s+\S/;
const DIFF_HUNK_RE = /^[+\-~] [-~ ]/;

export function isFeatureTitleLine(text: string): boolean {
    return TITLE_RE.test(text) && !DIFF_HUNK_RE.test(text);
}

export function indentOf(text: string): number {
    let n = 0;
    while (n < text.length && text[n] === ' ') n++;
    return n;
}

export interface FeatureLine {
    line: number;   // 0-based line number of the title
    indent: number; // leading-space count
}

/** All feature title lines in document order. */
export function collectFeatureLines(document: vscode.TextDocument): FeatureLine[] {
    const out: FeatureLine[] = [];
    for (let i = 0; i < document.lineCount; i++) {
        const text = document.lineAt(i).text;
        if (isFeatureTitleLine(text)) out.push({ line: i, indent: indentOf(text) });
    }
    return out;
}

/**
 * Title lines of the feature whose block contains `cursorLine`, plus every
 * descendant title (deeper indent). Stops at the first sibling/ancestor.
 * Used to fold/unfold a whole subtree's attribute blocks at once.
 */
export function subtreeTitleLines(
    document: vscode.TextDocument,
    cursorLine: number,
): number[] {
    const titles = collectFeatureLines(document);
    if (titles.length === 0) return [];

    // Nearest title at or above the cursor.
    let idx = -1;
    for (let i = 0; i < titles.length; i++) {
        if (titles[i].line <= cursorLine) idx = i;
        else break;
    }
    if (idx < 0) return [];

    const baseIndent = titles[idx].indent;
    const result = [titles[idx].line];
    for (let i = idx + 1; i < titles.length; i++) {
        if (titles[i].indent > baseIndent) result.push(titles[i].line);
        else break; // sibling or ancestor — subtree ends here
    }
    return result;
}
