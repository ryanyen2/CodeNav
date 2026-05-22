import * as vscode from 'vscode';

export class CodocConflictProvider implements vscode.TextDocumentContentProvider {
    private store = new Map<string, string>();

    private _onDidChange = new vscode.EventEmitter<vscode.Uri>();
    readonly onDidChange: vscode.Event<vscode.Uri> = this._onDidChange.event;

    provideTextDocumentContent(uri: vscode.Uri): string {
        return this.store.get(uri.toString()) || '';
    }

    setContent(uri: vscode.Uri, content: string): void {
        this.store.set(uri.toString(), content);
        this._onDidChange.fire(uri);
    }

    dispose(): void {
        this._onDidChange.dispose();
    }
}
