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

        const { features, proposals } = parseTreeCodoc(document.getText());

        // RETIRE/AMEND emit no text; they ride in the sidecar and decorate the
        // live node in place. Surface their Accept/Reject on that node's line.
        const overlay = this.state.sidecar.proposals?.by_feature ?? {};
        const lineById = new Map<string, number>();
        for (const f of features) if (f.id) lineById.set(f.id, f.line);

        type Entry = { line: number; eventId: string; verb: string };
        const entries: Entry[] = [];
        for (const p of proposals) {  // ADD/MOVE ghosts (text hunks)
            entries.push({ line: p.line, eventId: p.eventId, verb: p.op });
        }
        for (const [fid, prop] of Object.entries(overlay)) {  // RETIRE/AMEND on the node
            const line = lineById.get(fid);
            if (line !== undefined) entries.push({ line, eventId: prop.event_id, verb: prop.op });
        }

        const ids = entries.map(e => e.eventId);
        const count = entries.length;

        // Row-0 status + bulk actions.
        lenses.push(new vscode.CodeLens(top, { title: this._statusTitle(count), command: '' }));
        lenses.push(new vscode.CodeLens(top, { title: '$(sync) Sync', command: 'codoc.sync' }));
        if (count > 1) {
            lenses.push(new vscode.CodeLens(top, {
                title: `$(check-all) Accept all (${count})`,
                command: 'codoc.acceptAll', arguments: [ids],
            }));
            lenses.push(new vscode.CodeLens(top, {
                title: `$(close-all) Reject all (${count})`,
                command: 'codoc.rejectAll', arguments: [ids],
            }));
        }

        // Per-proposal Accept / Reject (on the ghost line, or on the live node).
        for (const e of entries) {
            const range = new vscode.Range(e.line, 0, e.line, 0);
            lenses.push(new vscode.CodeLens(range, {
                title: '$(check) Accept', tooltip: `Accept this ${e.verb}`,
                command: 'codoc.acceptProposal', arguments: [e.eventId],
            }));
            lenses.push(new vscode.CodeLens(range, {
                title: '$(x) Reject', tooltip: `Reject this ${e.verb}`,
                command: 'codoc.rejectProposal', arguments: [e.eventId],
            }));
        }
        return lenses;
    }

    private _statusTitle(pending: number): string {
        const st = this.state.status.state;
        if (st === 'realizing') return '$(loading~spin) codoc: implementing tree edits…';
        if (st === 'tree_dirty') return '$(pencil) codoc: applying tree edits…';
        if (pending > 0 || st === 'code_drift') return `$(bell) codoc: ${pending} proposed change${pending === 1 ? '' : 's'} — shown in the tree`;
        return '$(check) codoc: in sync';
    }
}
