import * as vscode from 'vscode';
import { WorkspaceState } from '../state/workspace-state';
import { parseTreeCodoc } from '../state/tree-model';
import { bindingsForFeature } from '../state/bindings-model';

/**
 * Derived code bindings (computed by Loop A) ride in tree.bindings.json, not in
 * the text. We surface them as subtle inlay-hint chips at the end of each
 * feature's title line — "where the feature touches code" without polluting the
 * authored prose. Authored citations are different: they live inline as
 * [label](codoc:file#symbol) markdown links (see doc-links / completion).
 */
const MAX_CHIPS = 3;

function leaf(symbol: string): string {
    const i = symbol.indexOf('::');
    const tail = i >= 0 ? symbol.slice(i + 2) : symbol;
    return tail === '__module__' ? '‹module›' : tail;
}

export class CodocInlayHintsProvider implements vscode.InlayHintsProvider {
    private _onDidChange = new vscode.EventEmitter<void>();
    readonly onDidChangeInlayHints = this._onDidChange.event;

    constructor(private state: WorkspaceState) {
        state.onDidChange(() => this._onDidChange.fire());
    }

    provideInlayHints(document: vscode.TextDocument): vscode.InlayHint[] {
        if (document.languageId !== 'codoc') return [];
        const hints: vscode.InlayHint[] = [];
        // Parse the live buffer so line numbers track unsaved edits.
        const { features } = parseTreeCodoc(document.getText());

        for (const f of features) {
            if (!f.id || f.retired) continue;
            const binds = bindingsForFeature(this.state.sidecar, f.id);
            if (binds.length === 0) continue;

            const shown = binds.slice(0, MAX_CHIPS);
            const extra = binds.length - shown.length;

            // Build clickable label parts: prefix + one part per chip + overflow count.
            const parts: vscode.InlayHintLabelPart[] = [];
            const prefix = new vscode.InlayHintLabelPart('  ↪ ');
            parts.push(prefix);
            for (let i = 0; i < shown.length; i++) {
                const b = shown[i];
                const chipText = `${b.file}:${leaf(b.symbol)}`;
                const part = new vscode.InlayHintLabelPart(chipText);
                // Clicking a chip opens the file beside and scrolls to the symbol.
                part.command = {
                    command: 'codoc.openRef',
                    title: `Open ${chipText}`,
                    arguments: [b.file, b.symbol],
                };
                part.tooltip = `Open ${b.file} › ${b.symbol}`;
                parts.push(part);
                if (i < shown.length - 1) parts.push(new vscode.InlayHintLabelPart('  '));
            }
            if (extra > 0) parts.push(new vscode.InlayHintLabelPart(`  +${extra}`));

            const lineLen = document.lineAt(f.line).text.length;
            const hint = new vscode.InlayHint(new vscode.Position(f.line, lineLen), parts);
            hint.paddingLeft = true;
            hint.tooltip = new vscode.MarkdownString(
                `**Code bindings** (${binds.length})\n\n` +
                binds.map(b => `- \`${b.file}:${leaf(b.symbol)}\``).join('\n'),
            );
            hints.push(hint);
        }
        return hints;
    }
}
