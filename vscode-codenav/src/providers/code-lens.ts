import * as vscode from 'vscode';
import { ServerState } from '../state/server';

export class CodocCodeLensProvider implements vscode.CodeLensProvider {
    constructor(private server: ServerState) {}

    provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
        if (!this.server.connected) return [];

        const lenses: vscode.CodeLens[] = [];
        for (let i = 0; i < document.lineCount; i++) {
            const line = document.lineAt(i).text;
            const isDef = /^\s*(def |class |function |async def )/.test(line);
            if (isDef) {
                const range = new vscode.Range(i, 0, i, 0);
                lenses.push(new vscode.CodeLens(range, {
                    title: 'codoc: find features',
                    command: 'codoc.openPanel',
                    tooltip: 'Open codoc panel and browse features for this file',
                }));
            }
        }
        return lenses;
    }
}
