import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { WorkspaceState } from './state/workspace-state';
import { CodocCodeLensProvider } from './providers/code-lens';
import { CodocFoldingProvider } from './providers/folding';
import { CodocSymbolProvider } from './providers/symbol';
import { FeatureTreeProvider } from './providers/feature-tree-view';
import { applyDecorations, createDecorations } from './providers/decoration';
import { subtreeTitleLines } from './providers/feature-lines';

export function activate(context: vscode.ExtensionContext): void {
    const state = new WorkspaceState(context);

    const diagnostics = vscode.languages.createDiagnosticCollection('codoc');
    context.subscriptions.push(diagnostics);

    // ── Feature tree panel ───────────────────────────────────────────────────
    const featureTreeProvider = new FeatureTreeProvider(state);
    const featureTreeView = vscode.window.createTreeView('codoc.featureTree', {
        treeDataProvider: featureTreeProvider,
        showCollapseAll: true,
    });
    context.subscriptions.push(featureTreeView);

    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.refreshFeatureTree', () => {
            featureTreeProvider.refresh();
        }),
    );

    // ── codoc.open — open tree.codoc ─────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.open', async () => {
            if (!state.rootDir) {
                await vscode.window.showInformationMessage(
                    'No codoc tree found. Run `codoc init` in the terminal to initialize.',
                );
                return;
            }
            const treePath = path.join(state.rootDir, '.codoc', 'tree.codoc');
            if (!fs.existsSync(treePath)) {
                await vscode.window.showInformationMessage(
                    'tree.codoc not found — run `codoc init` first.',
                );
                return;
            }
            const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(treePath));
            await vscode.window.showTextDocument(doc);
        }),
    );

    // ── codoc.sync — run codoc sync via terminal ──────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.sync', async () => {
            if (!state.rootDir) {
                await vscode.window.showInformationMessage(
                    'No codoc tree found. Run `codoc init` first.',
                );
                return;
            }
            // Open a terminal and run `codoc sync` (the file-watching daemon handles changes).
            const terminal = vscode.window.createTerminal({ name: 'codoc sync', cwd: state.rootDir });
            terminal.show();
            terminal.sendText('codoc sync');
        }),
    );

    // ── codoc.navigateToFeature ───────────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.navigateToFeature', async (titleOrId: string | null) => {
            if (!state.rootDir || !titleOrId) return;
            const treePath = path.join(state.rootDir, '.codoc', 'tree.codoc');
            if (!fs.existsSync(treePath)) return;
            const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(treePath));
            const editor = await vscode.window.showTextDocument(doc);
            for (let i = 0; i < doc.lineCount; i++) {
                if (doc.lineAt(i).text.includes(titleOrId)) {
                    const pos = new vscode.Position(i, 0);
                    editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
                    editor.selection = new vscode.Selection(pos, pos);
                    break;
                }
            }
        }),
    );

    // ── Folding commands ─────────────────────────────────────────────────────
    const isCodocEditor = (ed?: vscode.TextEditor): ed is vscode.TextEditor =>
        !!ed && ed.document.languageId === 'codoc';

    const foldAllAttributes = (): void => {
        setTimeout(() => void vscode.commands.executeCommand('editor.foldAll'), 200);
    };

    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.collapseAllFeatures', async () => {
            if (!isCodocEditor(vscode.window.activeTextEditor)) return;
            await vscode.commands.executeCommand('editor.foldAll');
        }),
        vscode.commands.registerCommand('codoc.expandAllFeatures', async () => {
            if (!isCodocEditor(vscode.window.activeTextEditor)) return;
            await vscode.commands.executeCommand('editor.unfoldAll');
        }),
        vscode.commands.registerCommand('codoc.collapseFeatureSubtree', async () => {
            const ed = vscode.window.activeTextEditor;
            if (!isCodocEditor(ed)) return;
            const lines = subtreeTitleLines(ed.document, ed.selection.active.line);
            if (lines.length) await vscode.commands.executeCommand('editor.fold', { selectionLines: lines });
        }),
        vscode.commands.registerCommand('codoc.expandFeatureSubtree', async () => {
            const ed = vscode.window.activeTextEditor;
            if (!isCodocEditor(ed)) return;
            const lines = subtreeTitleLines(ed.document, ed.selection.active.line);
            if (lines.length) await vscode.commands.executeCommand('editor.unfold', { selectionLines: lines });
        }),
    );

    // Auto-fold attributes on first open.
    const autoFolded = new Set<string>();
    const maybeAutoFold = async (ed?: vscode.TextEditor): Promise<void> => {
        if (!isCodocEditor(ed)) return;
        const cfg = vscode.workspace.getConfiguration('codoc');
        if (!cfg.get<boolean>('foldAttributesOnOpen', true)) return;
        const key = ed.document.uri.toString();
        if (autoFolded.has(key)) return;
        autoFolded.add(key);
        foldAllAttributes();
    };
    context.subscriptions.push(
        vscode.window.onDidChangeActiveTextEditor(ed => void maybeAutoFold(ed)),
    );
    void maybeAutoFold(vscode.window.activeTextEditor);

    // ── Code lens on source files ────────────────────────────────────────────
    context.subscriptions.push(
        vscode.languages.registerCodeLensProvider(
            [{ language: 'python' }, { language: 'typescript' }, { language: 'javascript' }],
            new CodocCodeLensProvider(state),
        ),
    );

    // ── Codoc language providers ─────────────────────────────────────────────
    const codocSelector: vscode.DocumentSelector = { language: 'codoc' };
    context.subscriptions.push(
        vscode.languages.registerFoldingRangeProvider(codocSelector, new CodocFoldingProvider()),
        vscode.languages.registerDocumentSymbolProvider(codocSelector, new CodocSymbolProvider()),
    );

    // ── Decorations ──────────────────────────────────────────────────────────
    const decorations = createDecorations(context);
    const refreshDecorations = (editor?: vscode.TextEditor): void => {
        const ed = editor ?? vscode.window.activeTextEditor;
        if (ed) applyDecorations(ed, decorations);
    };
    refreshDecorations();
    context.subscriptions.push(
        vscode.window.onDidChangeActiveTextEditor(refreshDecorations),
        vscode.workspace.onDidChangeTextDocument(e => {
            const ed = vscode.window.activeTextEditor;
            if (ed && ed.document === e.document) refreshDecorations(ed);
        }),
    );

    // Refresh decorations and tree view when the state changes.
    state.onDidChange(() => {
        refreshDecorations();
        featureTreeProvider.refresh();
    });
}

export function deactivate(): void {
    // All disposables registered on context.subscriptions.
}
