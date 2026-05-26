/**
 * AgentGutter — live gutter icons showing which features the coding agent is
 * currently touching, driven by activity.json.
 *
 * Pulses while epoch.open by cycling between two decoration intensities on a
 * ~700ms interval (VS Code decorations can't animate; swapping fakes it).
 */
import * as vscode from 'vscode';
import * as path from 'path';
import { WorkspaceState } from '../state/workspace-state';

const PULSE_INTERVAL_MS = 700;

export class AgentGutter {
    private _interval: ReturnType<typeof setInterval> | null = null;
    private _bright = true;

    // We need two gutter icon decoration types for pulsing.
    private _gutterWrite1: vscode.TextEditorDecorationType;
    private _gutterWrite2: vscode.TextEditorDecorationType;
    private _gutterRead: vscode.TextEditorDecorationType;

    constructor(
        private state: WorkspaceState,
        context: vscode.ExtensionContext,
    ) {
        // Create gutter icon decorations using SVG files (no codicon API for gutter).
        this._gutterWrite1 = vscode.window.createTextEditorDecorationType({
            gutterIconPath: this._iconUri(context, 'zap-bright'),
            gutterIconSize: 'contain',
            overviewRulerColor: new vscode.ThemeColor('charts.yellow'),
            overviewRulerLane: vscode.OverviewRulerLane.Left,
        });
        this._gutterWrite2 = vscode.window.createTextEditorDecorationType({
            gutterIconPath: this._iconUri(context, 'zap-dim'),
            gutterIconSize: 'contain',
        });
        this._gutterRead = vscode.window.createTextEditorDecorationType({
            gutterIconPath: this._iconUri(context, 'eye-dim'),
            gutterIconSize: 'contain',
        });

        context.subscriptions.push(this._gutterWrite1, this._gutterWrite2, this._gutterRead);
    }

    private _iconUri(context: vscode.ExtensionContext, name: string): vscode.Uri {
        return vscode.Uri.file(path.join(context.extensionPath, 'media', `gutter-${name}.svg`));
    }

    /** Called when state reloads; starts/stops the pulse and updates decorations. */
    update(): void {
        const enabled = vscode.workspace.getConfiguration('codoc').get<boolean>('agentGutter', true);
        if (!enabled || !this.state.agentActive) {
            this._stopPulse();
            this._clearGutterDecorations();
            return;
        }
        this._startPulse();
        this._applyGutterDecorations();
    }

    private _startPulse(): void {
        if (this._interval) return;
        this._interval = setInterval(() => {
            this._bright = !this._bright;
            this._applyGutterDecorations();
        }, PULSE_INTERVAL_MS);
    }

    private _stopPulse(): void {
        if (this._interval) { clearInterval(this._interval); this._interval = null; }
    }

    private _applyGutterDecorations(): void {
        const ed = vscode.window.activeTextEditor;
        if (!ed || ed.document.languageId !== 'codoc') return;

        const activeLines = this.state.activeFeatureLines;
        const writeRanges = activeLines.map(l => new vscode.Range(l, 0, l, 0));

        // Bright phase: show gutter write icon; dim phase: show dim icon.
        if (this._bright) {
            ed.setDecorations(this._gutterWrite1, writeRanges);
            ed.setDecorations(this._gutterWrite2, []);
        } else {
            ed.setDecorations(this._gutterWrite1, []);
            ed.setDecorations(this._gutterWrite2, writeRanges);
        }
        ed.setDecorations(this._gutterRead, []);
    }

    private _clearGutterDecorations(): void {
        const ed = vscode.window.activeTextEditor;
        if (!ed) return;
        ed.setDecorations(this._gutterWrite1, []);
        ed.setDecorations(this._gutterWrite2, []);
        ed.setDecorations(this._gutterRead, []);
    }

    dispose(): void {
        this._stopPulse();
    }
}
