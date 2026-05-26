/**
 * DependencyFocus — cursor-driven opacity dimming.
 *
 * When the cursor rests on a feature that has dependency edges, that feature
 * and all its graph-neighbours stay at opacity 1.0; every other feature block
 * dims to 0.45.  A feature with no edges never triggers dimming.
 */
import * as vscode from 'vscode';
import { WorkspaceState } from '../state/workspace-state';
import { CodocDecorations } from './decoration';
import { collectFeatureLines } from './feature-lines';

const DEBOUNCE_MS = 120;

export class DependencyFocus {
    private _timer: ReturnType<typeof setTimeout> | null = null;
    private _disposables: vscode.Disposable[] = [];

    constructor(
        private state: WorkspaceState,
        private dec: CodocDecorations,
        context: vscode.ExtensionContext,
    ) {
        const listener = vscode.window.onDidChangeTextEditorSelection(e => {
            if (e.textEditor.document.languageId !== 'codoc') return;
            if (this._timer) clearTimeout(this._timer);
            this._timer = setTimeout(() => this._update(e.textEditor), DEBOUNCE_MS);
        });
        const blurListener = vscode.window.onDidChangeActiveTextEditor(ed => {
            if (!ed || ed.document.languageId !== 'codoc') this._clearAll();
        });
        this._disposables = [listener, blurListener];
        context.subscriptions.push(...this._disposables);
    }

    private _update(editor: vscode.TextEditor): void {
        const enabled = vscode.workspace.getConfiguration('codoc').get<boolean>('focusDependencies', true);
        if (!enabled) { this._clearAll(editor); return; }

        const adj = this.state.featureEdges;
        const features = this.state.features;
        if (features.length === 0) { this._clearAll(editor); return; }

        const cursorLine = editor.selection.active.line;
        const titleLines = collectFeatureLines(editor.document).map(f => f.line);

        // Find the nearest title at or above the cursor.
        let focusedLine: number | null = null;
        for (let i = titleLines.length - 1; i >= 0; i--) {
            if (titleLines[i] <= cursorLine) { focusedLine = titleLines[i]; break; }
        }
        if (focusedLine === null) { this._clearAll(editor); return; }

        // Map line → feature
        const lineToFeature = new Map(features.map(f => [f.line, f]));
        const focused = lineToFeature.get(focusedLine);
        if (!focused?.id) { this._clearAll(editor); return; }

        const neighbors = adj.get(focused.id);
        if (!neighbors || neighbors.size === 0) { this._clearAll(editor); return; }

        // related = focused + its neighbors
        const related = new Set([focused.id, ...neighbors]);

        // Compute block spans: [titleLine, nextTitleLine) for each feature
        const sortedLines = [...titleLines].sort((a, b) => a - b);
        const dimRanges: vscode.Range[] = [];
        for (let i = 0; i < sortedLines.length; i++) {
            const tl = sortedLines[i];
            const feat = lineToFeature.get(tl);
            if (!feat?.id || related.has(feat.id)) continue;
            const endLine = i + 1 < sortedLines.length ? sortedLines[i + 1] - 1 : editor.document.lineCount - 1;
            dimRanges.push(new vscode.Range(tl, 0, endLine, Number.MAX_SAFE_INTEGER));
        }
        editor.setDecorations(this.dec.dimmed, dimRanges);
    }

    private _clearAll(editor?: vscode.TextEditor): void {
        const ed = editor ?? vscode.window.activeTextEditor;
        if (ed) ed.setDecorations(this.dec.dimmed, []);
    }

    /** Call when state reloads to rebuild adjacency. */
    refresh(): void {
        const ed = vscode.window.activeTextEditor;
        if (ed?.document.languageId === 'codoc') this._update(ed);
    }

    dispose(): void {
        for (const d of this._disposables) d.dispose();
    }
}
