import * as vscode from 'vscode';
import { DIFF_INTRO_PREFIX, DIFF_RETIRE_PREFIX, DIFF_AMEND_PREFIX } from './decoration';

export class CodocCodeActionProvider implements vscode.CodeActionProvider {
    provideCodeActions(
        document: vscode.TextDocument,
        range: vscode.Range,
        _context: vscode.CodeActionContext,
        _token: vscode.CancellationToken,
    ): vscode.CodeAction[] {
        if (document.languageId !== 'codoc') return [];

        const line = document.lineAt(range.start.line).text;
        if (!line.startsWith(DIFF_INTRO_PREFIX) && !line.startsWith(DIFF_RETIRE_PREFIX) && !line.startsWith(DIFF_AMEND_PREFIX)) {
            return [];
        }

        const acceptAction = new vscode.CodeAction(
            '✓ Accept proposal',
            vscode.CodeActionKind.QuickFix,
        );
        acceptAction.command = {
            command: 'codoc.acceptProposalAtLine',
            arguments: [range.start.line],
            title: 'Accept',
        };

        const rejectAction = new vscode.CodeAction(
            '✗ Reject proposal',
            vscode.CodeActionKind.QuickFix,
        );
        rejectAction.command = {
            command: 'codoc.rejectProposalAtLine',
            arguments: [range.start.line],
            title: 'Reject',
        };

        return [acceptAction, rejectAction];
    }
}
