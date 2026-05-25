import * as vscode from 'vscode';

/**
 * Quick-fix Accept / Reject on a proposal diff hunk (the same verdicts the
 * CodeLens offers, for users who reach for the lightbulb). The event id is
 * recovered from the hidden ⟨e-id⟩ on the hunk's title row.
 */
const DIFF_HUNK_RE = /^[+\-~] [-~] /;
const EVENT_ID_RE = /⟨(e-[0-9a-f]+)⟩/;

export class CodocCodeActionProvider implements vscode.CodeActionProvider {
    provideCodeActions(document: vscode.TextDocument, range: vscode.Range): vscode.CodeAction[] {
        if (document.languageId !== 'codoc') return [];
        const line = document.lineAt(range.start.line).text;
        if (!DIFF_HUNK_RE.test(line)) return [];
        const ev = EVENT_ID_RE.exec(line);
        if (!ev) return [];
        const eventId = ev[1];

        const accept = new vscode.CodeAction('$(check) Accept proposal', vscode.CodeActionKind.QuickFix);
        accept.command = { command: 'codoc.acceptProposal', title: 'Accept', arguments: [eventId] };

        const reject = new vscode.CodeAction('$(x) Reject proposal', vscode.CodeActionKind.QuickFix);
        reject.command = { command: 'codoc.rejectProposal', title: 'Reject', arguments: [eventId] };

        return [accept, reject];
    }
}
