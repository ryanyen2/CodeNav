import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { WorkspaceState } from './state/workspace-state';
import { CodocCodeLensProvider } from './providers/code-lens';
import { CodocTreeLensProvider } from './providers/codoc-tree-lens';
import { CodocCodeActionProvider } from './providers/code-actions';
import { CodocCompletionProvider } from './providers/completion';
import { CodocDocumentLinkProvider } from './providers/doc-links';
import { CodocInlayHintsProvider } from './providers/inlay';
import { CodocFoldingProvider } from './providers/folding';
import { CodocSymbolProvider } from './providers/symbol';
import { FeatureTreeProvider } from './providers/feature-tree-view';
import { applyDecorations, createDecorations } from './providers/decoration';
import { subtreeTitleLines } from './providers/feature-lines';

export function activate(context: vscode.ExtensionContext): void {
    const state = new WorkspaceState(context);
    const codocSelector: vscode.DocumentSelector = { language: 'codoc' };

    // ── Feature tree panel ───────────────────────────────────────────────────
    const featureTreeProvider = new FeatureTreeProvider(state);
    context.subscriptions.push(
        vscode.window.createTreeView('codoc.featureTree', { treeDataProvider: featureTreeProvider, showCollapseAll: true }),
        vscode.commands.registerCommand('codoc.refreshFeatureTree', () => featureTreeProvider.refresh()),
    );

    // ── codoc.open ───────────────────────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.open', async () => {
            const treePath = state.rootDir && path.join(state.rootDir, '.codoc', 'tree.codoc');
            if (!treePath || !fs.existsSync(treePath)) {
                await vscode.window.showInformationMessage('No codoc tree found — run `codoc init` in the terminal first.');
                return;
            }
            await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(vscode.Uri.file(treePath)));
        }),
    );

    // ── codoc.sync — kick the daemon via the terminal ─────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.sync', () => {
            if (!state.rootDir) { void vscode.window.showInformationMessage('No codoc tree found. Run `codoc init` first.'); return; }
            const terminal = vscode.window.createTerminal({ name: 'codoc sync', cwd: state.rootDir });
            terminal.show();
            terminal.sendText('codoc sync');
        }),
    );

    // ── Proposal verdicts → .codoc/inbox.json (the daemon applies them) ───────
    const verdict = (ids: string[] | string, accept: boolean): void => {
        state.writeVerdict(Array.isArray(ids) ? ids : [ids], accept);
    };
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.acceptProposal', (id: string) => verdict(id, true)),
        vscode.commands.registerCommand('codoc.rejectProposal', (id: string) => verdict(id, false)),
        vscode.commands.registerCommand('codoc.acceptAll', (ids: string[]) => verdict(ids, true)),
        vscode.commands.registerCommand('codoc.rejectAll', (ids: string[]) => verdict(ids, false)),
    );

    // ── codoc.openRef — jump from an inline [..](codoc:file#symbol) to code ───
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.openRef', async (file: string, symbol: string) => {
            if (!state.rootDir) return;
            const uri = vscode.Uri.file(path.join(state.rootDir, file));
            const doc = await vscode.workspace.openTextDocument(uri);
            const editor = await vscode.window.showTextDocument(doc);
            if (!symbol) return;
            const leaf = symbol.includes('.') ? symbol.split('.').pop()! : symbol;
            const re = new RegExp(`(?:def|class|function|const|let|var)\\s+${leaf}\\b|\\b${leaf}\\s*[=:(]`);
            for (let i = 0; i < doc.lineCount; i++) {
                if (re.test(doc.lineAt(i).text)) {
                    const pos = new vscode.Position(i, 0);
                    editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
                    editor.selection = new vscode.Selection(pos, pos);
                    break;
                }
            }
        }),
    );

    // ── codoc.navigateToFeature ───────────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.navigateToFeature', async (titleOrId: string | null) => {
            const treePath = state.rootDir && path.join(state.rootDir, '.codoc', 'tree.codoc');
            if (!treePath || !titleOrId || !fs.existsSync(treePath)) return;
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
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.collapseAllFeatures', async () => {
            if (isCodocEditor(vscode.window.activeTextEditor)) await vscode.commands.executeCommand('editor.foldAll');
        }),
        vscode.commands.registerCommand('codoc.expandAllFeatures', async () => {
            if (isCodocEditor(vscode.window.activeTextEditor)) await vscode.commands.executeCommand('editor.unfoldAll');
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

    // Auto-fold attribute blocks on first open (table-of-contents view).
    const autoFolded = new Set<string>();
    const maybeAutoFold = (ed?: vscode.TextEditor): void => {
        if (!isCodocEditor(ed)) return;
        if (!vscode.workspace.getConfiguration('codoc').get<boolean>('foldAttributesOnOpen', true)) return;
        const key = ed.document.uri.toString();
        if (autoFolded.has(key)) return;
        autoFolded.add(key);
        setTimeout(() => void vscode.commands.executeCommand('editor.foldAll'), 200);
    };
    context.subscriptions.push(vscode.window.onDidChangeActiveTextEditor(maybeAutoFold));
    maybeAutoFold(vscode.window.activeTextEditor);

    // ── Source-file code lens (which feature owns this symbol) ────────────────
    context.subscriptions.push(
        vscode.languages.registerCodeLensProvider(
            [{ language: 'python' }, { language: 'typescript' }, { language: 'javascript' }],
            new CodocCodeLensProvider(state),
        ),
    );

    // ── tree.codoc language providers ─────────────────────────────────────────
    context.subscriptions.push(
        vscode.languages.registerCodeLensProvider(codocSelector, new CodocTreeLensProvider(state)),
        vscode.languages.registerCodeActionsProvider(codocSelector, new CodocCodeActionProvider(),
            { providedCodeActionKinds: [vscode.CodeActionKind.QuickFix] }),
        vscode.languages.registerCompletionItemProvider(codocSelector, new CodocCompletionProvider(state), '[', '#', ':'),
        vscode.languages.registerDocumentLinkProvider(codocSelector, new CodocDocumentLinkProvider()),
        vscode.languages.registerInlayHintsProvider(codocSelector, new CodocInlayHintsProvider(state)),
        vscode.languages.registerFoldingRangeProvider(codocSelector, new CodocFoldingProvider()),
        vscode.languages.registerDocumentSymbolProvider(codocSelector, new CodocSymbolProvider()),
    );

    // ── Decorations (hide ids, colour diff hunks, strike retired) ─────────────
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

    state.onDidChange(() => {
        refreshDecorations();
        featureTreeProvider.refresh();
    });
}

export function deactivate(): void {
    // All disposables registered on context.subscriptions.
}
