import * as vscode from 'vscode';

export class ProposalsStatusBar {
    private item: vscode.StatusBarItem;

    constructor() {
        this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 200);
    }

    update(pendingCount: number): void {
        if (pendingCount === 0) {
            this.item.hide();
        } else {
            this.item.text = `$(bell) codoc: ${pendingCount} pending`;
            this.item.tooltip = 'Click to accept or reject all codoc proposals';
            this.item.command = 'codoc.showProposalActions';
            this.item.show();
        }
    }

    dispose(): void {
        this.item.dispose();
    }
}
