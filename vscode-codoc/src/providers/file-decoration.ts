/**
 * CodocFileDecorationProvider — badges source files in the Explorer while the
 * coding agent is editing them (from activity.json touched entries).
 *
 * Shows a badge + color on any file currently in activity.touched.
 * Cleared automatically when the epoch closes.
 */
import * as vscode from 'vscode';
import * as path from 'path';
import { WorkspaceState } from '../state/workspace-state';

export class CodocFileDecorationProvider implements vscode.FileDecorationProvider {
    private _onDidChangeFileDecorations = new vscode.EventEmitter<vscode.Uri | vscode.Uri[] | undefined>();
    readonly onDidChangeFileDecorations = this._onDidChangeFileDecorations.event;

    private _touchedUris = new Set<string>();

    constructor(private state: WorkspaceState) {}

    /** Call when state reloads. */
    update(): void {
        const prev = this._touchedUris;
        this._touchedUris = new Set();

        if (this.state.agentActive && this.state.rootDir) {
            for (const rel of Object.keys(this.state.activity.touched ?? {})) {
                const abs = path.join(this.state.rootDir, rel);
                this._touchedUris.add(vscode.Uri.file(abs).toString());
            }
        }

        // Fire for changed URIs.
        const changed = new Set([...prev, ...this._touchedUris]);
        if (changed.size > 0) {
            this._onDidChangeFileDecorations.fire(
                [...changed].map(u => vscode.Uri.parse(u))
            );
        }
    }

    provideFileDecoration(uri: vscode.Uri): vscode.FileDecoration | undefined {
        if (!this._touchedUris.has(uri.toString())) return undefined;
        return {
            badge: '●',
            color: new vscode.ThemeColor('charts.yellow'),
            tooltip: 'codoc: agent editing this file',
            propagate: false,
        };
    }
}
