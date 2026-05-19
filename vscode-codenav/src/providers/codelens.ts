import * as vscode from 'vscode';
import { ServerState } from '../state/server';

// Matches proposal lines: "? introduce: my-slug [Stub] # ?HLC"
const PROPOSAL_LINE_RE = /^\s*\?\s+([\w-]+):\s+(\S+)(?:\s+\[[^\]]+\])?\s*(?:#\s*\?([0-9a-zA-Z:\-_]+))?\s*$/;

export class CodocCodocCodeLensProvider implements vscode.CodeLensProvider {
    constructor(private server: ServerState) {}

    provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
        const lenses: vscode.CodeLens[] = [];

        // Header lenses on line 0: Sync, Render, Accept All, Reject All.
        const headerRange = new vscode.Range(0, 0, 0, 0);
        lenses.push(new vscode.CodeLens(headerRange, {
            title: '$(sync) Sync',
            command: 'codoc.syncFile',
            arguments: [document.uri],
        }));
        lenses.push(new vscode.CodeLens(headerRange, {
            title: '$(refresh) Render',
            command: 'codoc.render',
        }));
        lenses.push(new vscode.CodeLens(headerRange, {
            title: '$(git-commit) Reflect',
            command: 'codoc.reflect',
        }));

        // Count pending proposals to decide whether to show bulk lenses.
        let proposalCount = 0;
        for (let i = 0; i < document.lineCount; i++) {
            if (PROPOSAL_LINE_RE.test(document.lineAt(i).text)) proposalCount++;
        }
        if (proposalCount > 1) {
            lenses.push(new vscode.CodeLens(headerRange, {
                title: `$(check-all) Accept all (${proposalCount})`,
                command: 'codoc.acceptAll',
            }));
            lenses.push(new vscode.CodeLens(headerRange, {
                title: `$(trash) Reject all`,
                command: 'codoc.rejectAll',
            }));
        }

        // Per-proposal lenses: Accept, Edit & Accept, Reject.
        for (let i = 0; i < document.lineCount; i++) {
            const line = document.lineAt(i).text;
            const m = PROPOSAL_LINE_RE.exec(line);
            if (!m) continue;
            const proposedSlug = m[2];
            const hlc = m[3];
            if (!hlc) continue; // can't act without HLC
            const range = new vscode.Range(i, 0, i, line.length);

            lenses.push(new vscode.CodeLens(range, {
                title: '$(check) Accept',
                command: 'codoc.acceptProposal',
                arguments: [document.uri, hlc],
            }));
            lenses.push(new vscode.CodeLens(range, {
                title: '$(edit) Edit & Accept',
                command: 'codoc.acceptProposalWithEdits',
                arguments: [document.uri, hlc, proposedSlug],
            }));
            lenses.push(new vscode.CodeLens(range, {
                title: '$(x) Reject',
                command: 'codoc.rejectProposal',
                arguments: [document.uri, hlc],
            }));
        }
        return lenses;
    }
}
