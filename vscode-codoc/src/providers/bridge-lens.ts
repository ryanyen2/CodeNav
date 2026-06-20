/**
 * bridge-lens.ts — the live "this prose implicates this code" CodeLens (P2 / spec §A.2).
 *
 * While the user edits a feature's prose, the bridged code file shows, above each implicated
 * declaration line, a live lens:
 *   - `◇ implicated by "<title>" — handing off will rework this`  (still editing / captured)
 *   - `◆ queued — run /codoc:sync`                                 (committed → the pipeline is awaiting impl)
 * The `◇` (open diamond) / `◆` (filled diamond) are the doc-ahead glyph at its lifecycle weight
 * (shape carries the phase, green carries the direction — same grammar as the doc pane's icon
 * family). The lens recomputes whenever the bridge target changes (BridgeController fires
 * onDidChange) so it tracks the prose live. It is additive: the existing `codoc: ⟳ will change`
 * lens (code-lens.ts) still fires once committed; this one is the LIVE-editing companion.
 */
import * as vscode from 'vscode';
import { WorkspaceState } from '../state/workspace-state';
import { BridgeController } from './bridge-controller';
import { implicatedDeclLines } from '../state/bridge';

export class BridgeCodeLensProvider implements vscode.CodeLensProvider {
    private readonly _onDidChangeCodeLenses = new vscode.EventEmitter<void>();
    readonly onDidChangeCodeLenses = this._onDidChangeCodeLenses.event;

    constructor(
        private readonly state: WorkspaceState,
        private readonly bridge: BridgeController,
    ) {
        // Re-query the lens whenever the bridge target changes (prose edit / caret leave).
        bridge.onDidChange(() => this._onDidChangeCodeLenses.fire());
    }

    provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
        if (document.languageId === 'codoc') return [];
        const target = this.bridge.current;
        if (!target) return [];
        const rel = vscode.workspace.asRelativePath(document.fileName);
        if (rel !== target.file) return [];

        // committed? → the pipeline is awaiting impl/tree_dirty → the filled-diamond "queued" lens.
        const st = this.state.status.state;
        const committed = st === 'awaiting_impl' || st === 'tree_dirty';

        const lines: string[] = [];
        for (let i = 0; i < document.lineCount; i++) lines.push(document.lineAt(i).text);

        // §A.4 no-binding (file-level): a single ghost lens at the top of the likely target file.
        if (target.fileLevel) {
            const range = new vscode.Range(0, 0, 0, lines[0]?.length ?? 0);
            return [new vscode.CodeLens(range, {
                title: `◇ new code will be added here for "${target.title}"`,
                command: '',
                tooltip: 'This feature has no code yet — editing its prose plans new code here.',
            })];
        }

        const implicated = implicatedDeclLines(lines, target.leaves);
        const lenses: vscode.CodeLens[] = [];
        for (const ln of implicated) {
            const range = new vscode.Range(ln, 0, ln, lines[ln].length);
            lenses.push(new vscode.CodeLens(range, committed
                ? { title: '◆ queued — run /codoc:sync', command: 'codoc.sync', tooltip: 'Committed — run /codoc:sync to implement.' }
                : { title: `◇ implicated by "${target.title}" — handing off will rework this`, command: '', tooltip: 'The prose you are editing is about this code.' }));
        }
        return lenses;
    }
}
