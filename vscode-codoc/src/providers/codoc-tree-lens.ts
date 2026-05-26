import * as vscode from 'vscode';
import { WorkspaceState } from '../state/workspace-state';
import { parseTreeCodoc } from '../state/tree-model';

/**
 * CodeLens for the tree.codoc editor itself:
 *   • a status line on row 0 (in sync / proposals / implementing…) + Sync action;
 *   • Accept / Reject above every proposal diff hunk, plus Accept all / Reject all.
 *
 * Verdicts are written to .codoc/inbox.json (via WorkspaceState.writeVerdict) and
 * applied by the daemon — there is nothing to type, and the live tree is left
 * untouched until the change is accepted.
 */
export class CodocTreeLensProvider implements vscode.CodeLensProvider {
    private _onDidChange = new vscode.EventEmitter<void>();
    readonly onDidChangeCodeLenses = this._onDidChange.event;

    constructor(private state: WorkspaceState) {
        state.onDidChange(() => this._onDidChange.fire());
    }

    provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
        if (document.languageId !== 'codoc') return [];
        const lenses: vscode.CodeLens[] = [];
        const top = new vscode.Range(0, 0, 0, 0);

        const { proposals } = parseTreeCodoc(document.getText());
        const ids = proposals.map(p => p.eventId);

        // Row-0 status + bulk actions.
        lenses.push(new vscode.CodeLens(top, { title: this._statusTitle(proposals.length), command: '' }));
        lenses.push(new vscode.CodeLens(top, { title: '$(sync) Sync', command: 'codoc.sync' }));
        if (proposals.length > 1) {
            lenses.push(new vscode.CodeLens(top, {
                title: `$(check-all) Accept all (${proposals.length})`,
                command: 'codoc.acceptAll', arguments: [ids],
            }));
            lenses.push(new vscode.CodeLens(top, {
                title: `$(close-all) Reject all (${proposals.length})`,
                command: 'codoc.rejectAll', arguments: [ids],
            }));
        }

        // Per-proposal Accept / Reject.
        for (const p of proposals) {
            const range = new vscode.Range(p.line, 0, p.line, 0);
            const verb = p.op === 'retire' ? 'retire' : p.op === 'move' ? 'move' : p.op === 'amend' ? 'amend' : 'add';
            lenses.push(new vscode.CodeLens(range, {
                title: '$(check) Accept', tooltip: `Accept this ${verb}`,
                command: 'codoc.acceptProposal', arguments: [p.eventId],
            }));
            lenses.push(new vscode.CodeLens(range, {
                title: '$(x) Reject', tooltip: `Reject this ${verb}`,
                command: 'codoc.rejectProposal', arguments: [p.eventId],
            }));
        }
        return lenses;
    }

    private _statusTitle(pending: number): string {
        const st = this.state.status.state;
        if (st === 'realizing') return '$(loading~spin) codoc: implementing tree edits…';
        if (st === 'tree_dirty') return '$(pencil) codoc: applying tree edits…';
        if (pending > 0 || st === 'code_drift') return `$(bell) codoc: ${pending} proposed change${pending === 1 ? '' : 's'} — review below`;
        return '$(check) codoc: in sync';
    }
}
