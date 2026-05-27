/**
 * AgentGutter — a calm, static gutter marker showing which features the coding
 * agent is currently touching, driven by activity.json.
 *
 * No animation: a single static gutter icon on the active feature lines while
 * the agent is working. Peripheral by design.
 */
import * as vscode from 'vscode';
import * as path from 'path';
import { WorkspaceState } from '../state/workspace-state';

export class AgentGutter {
    private _gutterActive: vscode.TextEditorDecorationType;

    constructor(
        private state: WorkspaceState,
        context: vscode.ExtensionContext,
    ) {
        this._gutterActive = vscode.window.createTextEditorDecorationType({
            gutterIconPath: this._iconUri(context, 'zap-bright'),
            gutterIconSize: 'contain',
            overviewRulerColor: new vscode.ThemeColor('charts.yellow'),
            overviewRulerLane: vscode.OverviewRulerLane.Left,
        });

        context.subscriptions.push(
            this._gutterActive,
            vscode.window.onDidChangeActiveTextEditor(() => this.update()),
        );
    }

    private _iconUri(context: vscode.ExtensionContext, name: string): vscode.Uri {
        return vscode.Uri.file(path.join(context.extensionPath, 'media', `gutter-${name}.svg`));
    }

    /** Called when state reloads or the active editor changes. */
    update(): void {
        const ed = vscode.window.activeTextEditor;
        if (!ed || ed.document.languageId !== 'codoc') return;

        const enabled = vscode.workspace.getConfiguration('codoc').get<boolean>('agentGutter', true);
        if (!enabled || !this.state.agentActive) {
            ed.setDecorations(this._gutterActive, []);
            return;
        }

        const ranges = this.state.activeFeatureLines.map(l => new vscode.Range(l, 0, l, 0));
        ed.setDecorations(this._gutterActive, ranges);
    }

    dispose(): void {
        // No interval to tear down; the decoration type is disposed via context.subscriptions.
    }
}
