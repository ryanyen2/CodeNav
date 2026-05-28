import * as vscode from 'vscode';
import { WorkspaceState } from '../state/workspace-state';
import { parseTreeCodoc } from '../state/tree-model';
import { bindingsForFeature } from '../state/bindings-model';

/**
 * One small chip per feature: `9 refs`. Mirrors the Feature Tree panel's pill
 * so the editor row stays scannable. Click → quick pick of bindings; hover →
 * full list. (Earlier behaviour was N inline chips per feature, which wrapped
 * onto a second line on long ref lists and broke the visual title row.)
 *
 * Authored citations live inline as [label](codoc:file#symbol) markdown links
 * — those are a different surface (see doc-links / completion).
 */

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

            const label = binds.length === 1 ? '1 ref' : `${binds.length} refs`;
            const part = new vscode.InlayHintLabelPart(label);
            part.command = {
                command: 'codoc.pickBinding',
                title: 'Open a code binding',
                arguments: [f.id],
            };

            // Hover lists every binding as a clickable command link.
            const lines = binds.map(b => {
                const args = encodeURIComponent(JSON.stringify([b.file, b.symbol]));
                return `- [\`${b.file}:${leaf(b.symbol)}\`](command:codoc.openRef?${args})`;
            });
            const md = new vscode.MarkdownString(
                `**${binds.length} code binding${binds.length === 1 ? '' : 's'}**\n\n` + lines.join('\n'),
                true,
            );
            md.isTrusted = true;
            part.tooltip = md;

            const lineLen = document.lineAt(f.line).text.length;
            const hint = new vscode.InlayHint(new vscode.Position(f.line, lineLen), [part]);
            hint.paddingLeft = true;
            hints.push(hint);
        }
        return hints;
    }
}
