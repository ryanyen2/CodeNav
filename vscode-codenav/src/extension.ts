import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { CodocPanel } from './panel/panel';
import { ServerState } from './state/server';
import { CodocCodeLensProvider } from './providers/code-lens';
import { CodocFoldingProvider } from './providers/folding';
import { CodocSymbolProvider } from './providers/symbol';
import { CodocHoverProvider } from './providers/hover';
import { CodocCodocCodeLensProvider } from './providers/codelens';
import { CodocDefinitionProvider } from './providers/definition';
import { applyDecorations, createDecorations } from './providers/decoration';
import { scheduleSyncCodocFile, onSaveCodocFile } from './sync-on-save';

export function activate(context: vscode.ExtensionContext): void {
    const server = new ServerState(context);
    const diagnostics = vscode.languages.createDiagnosticCollection('codoc');
    context.subscriptions.push(diagnostics);

    // Refresh the pending-proposal count shown in the status bar.
    async function refreshProposalCount(): Promise<void> {
        if (!server.client) return;
        try {
            const pending = await server.client.listPending();
            server.setProposalCount(pending.length);
        } catch {
            // Server unreachable — ServerState health poll updates the display.
        }
    }

    // Navigation: open _index.codoc (used by status bar click).
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.openIndex', async () => {
            const rootDir = server.rootDir;
            if (!rootDir) {
                vscode.window.showInformationMessage('codoc: no workspace with .codoc/ found');
                return;
            }
            const indexPath = path.join(rootDir, '.codoc', 'tree', '_index.codoc');
            if (!fs.existsSync(indexPath)) {
                const choice = await vscode.window.showInformationMessage(
                    'codoc: tree not rendered yet.',
                    'Render now',
                );
                if (choice === 'Render now' && server.client) {
                    await server.client.renderTree();
                }
                return;
            }
            const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(indexPath));
            await vscode.window.showTextDocument(doc);
        }),
    );

    // Legacy panel command (kept for backward compatibility).
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.openPanel', () => {
            CodocPanel.createOrShow(context, server);
        }),
        vscode.commands.registerCommand('codoc.reflect', async () => {
            if (!server.client) {
                vscode.window.showInformationMessage('codoc server not connected.');
                return;
            }
            try {
                await server.client.reflect();
                vscode.window.showInformationMessage('codoc: reflect complete');
            } catch (e: unknown) {
                const msg = e instanceof Error ? e.message : String(e);
                vscode.window.showErrorMessage(`codoc reflect failed: ${msg}`);
            }
        }),
        vscode.commands.registerCommand('codoc.bootstrap', async () => {
            const panel = CodocPanel.current;
            if (panel) {
                await panel.bootstrap();
            } else {
                vscode.window.showInformationMessage('Open the codoc panel first.');
            }
        }),
    );

    // Projection commands.
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.render', async () => {
            if (!server.client) return;
            try {
                await server.client.renderTree();
                vscode.window.showInformationMessage('codoc: tree rendered');
                await refreshProposalCount();
            } catch (e: unknown) {
                const msg = e instanceof Error ? e.message : String(e);
                vscode.window.showErrorMessage(`codoc render failed: ${msg}`);
            }
        }),
        vscode.commands.registerCommand('codoc.syncFile', async (uri?: vscode.Uri) => {
            const target = uri
                ? vscode.workspace.textDocuments.find(d => d.uri.fsPath === uri.fsPath)
                : vscode.window.activeTextEditor?.document;
            if (target && target.languageId === 'codoc') {
                await onSaveCodocFile(target, server, diagnostics);
                await refreshProposalCount();
            }
        }),
    );

    // Proposal commands.
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.acceptProposal', async (_uri: vscode.Uri | undefined, hlc: string) => {
            if (!server.client || !hlc) return;
            try {
                await server.client.acceptProposal(hlc);
                vscode.window.showInformationMessage('codoc: proposal accepted');
                await server.client.renderTree();
                await refreshProposalCount();
            } catch (e: unknown) {
                const msg = e instanceof Error ? e.message : String(e);
                vscode.window.showErrorMessage(`codoc accept failed: ${msg}`);
            }
        }),
        vscode.commands.registerCommand('codoc.rejectProposal', async (_uri: vscode.Uri | undefined, hlc: string) => {
            if (!server.client || !hlc) return;
            try {
                await server.client.rejectProposal(hlc);
                vscode.window.showInformationMessage('codoc: proposal rejected');
                await server.client.renderTree();
                await refreshProposalCount();
            } catch (e: unknown) {
                const msg = e instanceof Error ? e.message : String(e);
                vscode.window.showErrorMessage(`codoc reject failed: ${msg}`);
            }
        }),
        vscode.commands.registerCommand('codoc.acceptProposalWithEdits', async (
            _uri: vscode.Uri | undefined,
            hlc: string,
            proposedSlug: string,
        ) => {
            if (!server.client || !hlc) return;

            const newSlug = await vscode.window.showInputBox({
                title: 'Edit proposed slug',
                value: proposedSlug,
                prompt: 'Press Enter to confirm, Escape to cancel',
            });
            if (newSlug === undefined) return; // user cancelled

            const edits: Record<string, unknown> = {};
            if (newSlug.trim() && newSlug.trim() !== proposedSlug) {
                edits.slug = newSlug.trim();
            }

            try {
                await server.client.acceptProposal(hlc, Object.keys(edits).length ? edits : undefined);
                vscode.window.showInformationMessage(
                    Object.keys(edits).length ? 'codoc: proposal accepted with edits' : 'codoc: proposal accepted',
                );
                await server.client.renderTree();
                await refreshProposalCount();
            } catch (e: unknown) {
                const msg = e instanceof Error ? e.message : String(e);
                vscode.window.showErrorMessage(`codoc accept failed: ${msg}`);
            }
        }),
    );

    // Bulk proposal commands.
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.acceptAll', async () => {
            if (!server.client) return;
            const confirm = await vscode.window.showInputBox({
                prompt: 'Accept ALL pending proposals? Type YES to confirm.',
                placeHolder: 'YES',
            });
            if (confirm !== 'YES') return;
            try {
                const result = await server.client.acceptAll();
                vscode.window.showInformationMessage(`codoc: accepted ${result.accepted} proposal(s)`);
                await server.client.renderTree();
                await refreshProposalCount();
            } catch (e: unknown) {
                const msg = e instanceof Error ? e.message : String(e);
                vscode.window.showErrorMessage(`codoc accept-all failed: ${msg}`);
            }
        }),
        vscode.commands.registerCommand('codoc.rejectAll', async () => {
            if (!server.client) return;
            const answer = await vscode.window.showWarningMessage(
                'Reject ALL pending proposals? This cannot be undone.',
                { modal: true },
                'Reject All',
            );
            if (answer !== 'Reject All') return;
            try {
                const result = await server.client.rejectAll();
                vscode.window.showInformationMessage(`codoc: rejected ${result.rejected} proposal(s)`);
                await server.client.renderTree();
                await refreshProposalCount();
            } catch (e: unknown) {
                const msg = e instanceof Error ? e.message : String(e);
                vscode.window.showErrorMessage(`codoc reject-all failed: ${msg}`);
            }
        }),
    );

    // Code lens on Python and TypeScript files.
    const codeLens = new CodocCodeLensProvider(server);
    context.subscriptions.push(
        vscode.languages.registerCodeLensProvider(
            [{ language: 'python' }, { language: 'typescript' }, { language: 'javascript' }],
            codeLens,
        ),
    );

    // Codoc language providers.
    const codocSelector: vscode.DocumentSelector = { language: 'codoc' };
    context.subscriptions.push(
        vscode.languages.registerFoldingRangeProvider(codocSelector, new CodocFoldingProvider()),
        vscode.languages.registerDocumentSymbolProvider(codocSelector, new CodocSymbolProvider()),
        vscode.languages.registerHoverProvider(codocSelector, new CodocHoverProvider(server)),
        vscode.languages.registerCodeLensProvider(codocSelector, new CodocCodocCodeLensProvider(server)),
        vscode.languages.registerDefinitionProvider(codocSelector, new CodocDefinitionProvider(server)),
    );

    // Decorations.
    const decorations = createDecorations();
    const refreshDecorations = (editor?: vscode.TextEditor) => {
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

    // On-save sync (debounced).
    context.subscriptions.push(
        vscode.workspace.onDidSaveTextDocument(doc => {
            if (doc.languageId === 'codoc') {
                scheduleSyncCodocFile(doc, server, diagnostics);
            }
        }),
    );

    // Proposal count: refresh on connect, then every 30s.
    server.onReady(() => refreshProposalCount());
    const countInterval = setInterval(refreshProposalCount, 30_000);
    context.subscriptions.push({ dispose: () => clearInterval(countInterval) });
}

export function deactivate(): void {
    CodocPanel.current?.dispose();
}
