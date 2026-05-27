import * as vscode from 'vscode';
import { WorkspaceState } from '../state/workspace-state';
import { parseTreeCodoc } from '../state/tree-model';

/**
 * Quick-fix Accept / Reject for the lightbulb (the same verdicts the CodeLens
 * offers). Two cases:
 *   • cursor on an ADD/MOVE ghost hunk → recover ⟨e-id⟩ from the line text.
 *   • cursor on a *live* node carrying a sidecar RETIRE/AMEND overlay → recover
 *     the event id from the sidecar's by_feature map (no ⟨e-id⟩ in the text).
 */
const DIFF_HUNK_RE = /^[+\-~] [-~] /;
const EVENT_ID_RE = /⟨(e-[0-9a-f]+)⟩/;

export class CodocCodeActionProvider implements vscode.CodeActionProvider {
    constructor(private state: WorkspaceState) {}

    provideCodeActions(document: vscode.TextDocument, range: vscode.Range): vscode.CodeAction[] {
        if (document.languageId !== 'codoc') return [];
        const lineNo = range.start.line;
        const line = document.lineAt(lineNo).text;

        // Case 1: ADD/MOVE ghost hunk — id is inline.
        if (DIFF_HUNK_RE.test(line)) {
            const ev = EVENT_ID_RE.exec(line);
            return ev ? this._actions(ev[1]) : [];
        }

        // Case 2: live node with a sidecar overlay (retire/amend).
        const overlay = this.state.sidecar.proposals?.by_feature ?? {};
        const { features } = parseTreeCodoc(document.getText());
        const feat = features.find(f => f.line === lineNo && f.id);
        if (feat?.id && overlay[feat.id]) {
            return this._actions(overlay[feat.id].event_id);
        }
        return [];
    }

    private _actions(eventId: string): vscode.CodeAction[] {
        const accept = new vscode.CodeAction('$(check) Accept proposal', vscode.CodeActionKind.QuickFix);
        accept.command = { command: 'codoc.acceptProposal', title: 'Accept', arguments: [eventId] };

        const reject = new vscode.CodeAction('$(x) Reject proposal', vscode.CodeActionKind.QuickFix);
        reject.command = { command: 'codoc.rejectProposal', title: 'Reject', arguments: [eventId] };

        return [accept, reject];
    }
}
