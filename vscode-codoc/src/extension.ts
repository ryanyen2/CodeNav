import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { ServerState } from './state/server';
import { LineTracker } from './state/line-tracker';
import { ProposalsStatusBar } from './state/proposals-status-bar';
import { CodocCodeLensProvider } from './providers/code-lens';
import { CodocFoldingProvider } from './providers/folding';
import { CodocSymbolProvider } from './providers/symbol';
import { CodocHoverProvider } from './providers/hover';
import { CodocCodocCodeLensProvider } from './providers/codelens';
import { CodocDefinitionProvider } from './providers/definition';
import { CodocCodeActionProvider } from './providers/code-actions';
import { CodocConflictProvider } from './providers/conflict-resolver';
import { applyDecorations, createDecorations } from './providers/decoration';
import { scheduleSyncCodocFile, onSaveCodocFile } from './sync-on-save';
import { LiveActivityTracker } from './state/live-activity';

export function activate(context: vscode.ExtensionContext): void {
    const server = new ServerState(context);
    const diagnostics = vscode.languages.createDiagnosticCollection('codoc');
    context.subscriptions.push(diagnostics);

    const proposalsBar = new ProposalsStatusBar();
    context.subscriptions.push(proposalsBar);

    const lineTracker = new LineTracker();

    // Refresh the pending-proposal count shown in the status bar.
    async function refreshProposalCount(): Promise<void> {
        if (!server.client) return;
        try {
            const pending = await server.client.listPending();
            server.setProposalCount(pending.length);
            proposalsBar.update(pending.length);
        } catch {
            // Server unreachable — ServerState health poll updates the display.
        }
    }

    // ── codoc.open — unified smart open command ──────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.open', async () => {
            if (!server.rootDir) {
                // No .codoc/ found — offer to init + bootstrap
                const confirm = await vscode.window.showInformationMessage(
                    'No codoc tree found in this workspace. Initialize and bootstrap now?',
                    'Yes', 'Cancel',
                );
                if (confirm !== 'Yes') return;
                await vscode.window.withProgress(
                    {
                        location: vscode.ProgressLocation.Notification,
                        title: 'codoc',
                        cancellable: false,
                    },
                    async (progress) => {
                        progress.report({ message: 'Initializing...' });
                        await server.client?.initRepo();
                        progress.report({ message: 'Bootstrapping codebase (this may take a minute)...' });
                        await server.client?.bootstrap();
                        progress.report({ message: 'Rendering tree...' });
                        await server.client?.renderTree();
                    },
                );
            } else {
                // .codoc/ exists — check if index exists and if stale
                if (server.client) {
                    try {
                        const state = await server.client.getRepoState();
                        if (!state?.hasIndex || state?.isStale) {
                            await server.client.renderTree();
                        }
                    } catch {
                        // Endpoint not yet implemented — just try to open
                    }
                }
            }

            if (!server.rootDir) return;

            const indexPath = path.join(server.rootDir, '.codoc', 'tree', '_index.codoc');
            if (fs.existsSync(indexPath)) {
                const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(indexPath));
                await vscode.window.showTextDocument(doc);
            }
            await refreshProposalCount();
        }),
    );

    // ── codoc.renderHard — explicit hard refresh ─────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.renderHard', async () => {
            if (!server.client) return;
            await vscode.window.withProgress(
                {
                    location: vscode.ProgressLocation.Notification,
                    title: 'codoc: Rendering...',
                    cancellable: false,
                },
                async () => { await server.client!.renderTree(); },
            );
            await refreshProposalCount();
            if (!server.rootDir) return;
            const indexPath = path.join(server.rootDir, '.codoc', 'tree', '_index.codoc');
            if (fs.existsSync(indexPath)) {
                const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(indexPath));
                await vscode.window.showTextDocument(doc);
            }
        }),
    );

    // ── Backward-compat: codoc.openIndex → codoc.open ───────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.openIndex', () => {
            return vscode.commands.executeCommand('codoc.open');
        }),
    );

    // ── codoc.reflect ────────────────────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.reflect', async () => {
            if (!server.client) {
                vscode.window.showInformationMessage('codoc server not connected.');
                return;
            }
            try {
                await server.client.reflect();
                vscode.window.showInformationMessage('codoc: reflect complete');
                await refreshProposalCount();
            } catch (e: unknown) {
                const msg = e instanceof Error ? e.message : String(e);
                vscode.window.showErrorMessage(`codoc reflect failed: ${msg}`);
            }
        }),
    );

    // ── codoc.bootstrap — direct HTTP, no panel ──────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.bootstrap', async () => {
            if (!server.client) {
                vscode.window.showInformationMessage('codoc server not connected.');
                return;
            }
            try {
                await vscode.window.withProgress(
                    {
                        location: vscode.ProgressLocation.Notification,
                        title: 'codoc: Bootstrapping (this may take a minute)...',
                        cancellable: false,
                    },
                    async () => { await server.client!.bootstrap(); },
                );
                vscode.window.showInformationMessage('codoc: bootstrap complete');
                await refreshProposalCount();
            } catch (e: unknown) {
                const msg = e instanceof Error ? e.message : String(e);
                vscode.window.showErrorMessage(`codoc bootstrap failed: ${msg}`);
            }
        }),
    );

    // ── Projection commands ──────────────────────────────────────────────────
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

    // ── Proposal commands ────────────────────────────────────────────────────
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

    // ── Bulk proposal commands ───────────────────────────────────────────────
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

    // ── Proposal actions widget (status bar click) ───────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.showProposalActions', async () => {
            const choice = await vscode.window.showQuickPick([
                { label: '$(check) Accept All', description: 'Accept all pending proposals' },
                { label: '$(x) Reject All', description: 'Reject all pending proposals' },
            ]);
            if (choice?.label.includes('Accept All')) {
                await vscode.commands.executeCommand('codoc.acceptAll');
            } else if (choice?.label.includes('Reject All')) {
                await vscode.commands.executeCommand('codoc.rejectAll');
            }
        }),
    );

    // ── Per-line proposal actions ────────────────────────────────────────────
    const HLC_RE = /#\s*\?([0-9a-zA-Z:\-_]+)/;

    function hlcForLine(document: vscode.TextDocument, lineNum: number): string | null {
        // Old format: inline "# ?<hlc>" comment on the proposal line.
        const line = document.lineAt(lineNum).text;
        const m = HLC_RE.exec(line);
        if (m) return m[1];

        // New format: col-0 diff hunks — HLC stored in tree.meta.json line_range_to_hlc.
        if (!server.rootDir) return null;
        const metaPath = path.join(server.rootDir, '.codoc', 'tree', 'tree.meta.json');
        try {
            const meta = JSON.parse(fs.readFileSync(metaPath, 'utf-8')) as {
                line_range_to_hlc?: Record<string, string>;
            };
            const lrMap = meta.line_range_to_hlc ?? {};
            const fileName = path.basename(document.fileName);
            for (const [key, hlc] of Object.entries(lrMap)) {
                const colonIdx = key.lastIndexOf(':');
                if (colonIdx === -1) continue;
                if (key.slice(0, colonIdx) !== fileName) continue;
                const rangePart = key.slice(colonIdx + 1);
                const dashIdx = rangePart.indexOf('-');
                const start = parseInt(rangePart.slice(0, dashIdx), 10);
                const end = parseInt(rangePart.slice(dashIdx + 1), 10);
                if (lineNum >= start && lineNum <= end) return hlc;
            }
        } catch {
            // meta file unreadable — fall through
        }
        return null;
    }

    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.acceptProposalAtLine', async (lineNum: number) => {
            if (!server.client) return;
            const editor = vscode.window.activeTextEditor;
            if (!editor) return;
            const hlc = hlcForLine(editor.document, lineNum);
            if (!hlc) {
                vscode.window.showWarningMessage('codoc: no HLC found on this line');
                return;
            }
            await vscode.commands.executeCommand('codoc.acceptProposal', editor.document.uri, hlc);
        }),
        vscode.commands.registerCommand('codoc.rejectProposalAtLine', async (lineNum: number) => {
            if (!server.client) return;
            const editor = vscode.window.activeTextEditor;
            if (!editor) return;
            const hlc = hlcForLine(editor.document, lineNum);
            if (!hlc) {
                vscode.window.showWarningMessage('codoc: no HLC found on this line');
                return;
            }
            await vscode.commands.executeCommand('codoc.rejectProposal', editor.document.uri, hlc);
        }),
    );

    // ── Navigate to feature in _index.codoc ─────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.navigateToFeature', async (slugOrTitle: string | null) => {
            if (!server.rootDir || !slugOrTitle) return;
            const indexPath = path.join(server.rootDir, '.codoc', 'tree', '_index.codoc');
            if (!fs.existsSync(indexPath)) return;
            const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(indexPath));
            const editor = await vscode.window.showTextDocument(doc);
            // Scan for the slug/title in the document
            for (let i = 0; i < doc.lineCount; i++) {
                const line = doc.lineAt(i).text;
                if (line.includes(slugOrTitle)) {
                    const pos = new vscode.Position(i, 0);
                    editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
                    editor.selection = new vscode.Selection(pos, pos);
                    break;
                }
            }
        }),
    );

    // ── Show bindings QuickPick for a feature slug ───────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.showBindingsForFeature', async (slug: string) => {
            if (!server.client || !server.rootDir) return;
            try {
                // Try to look up bindings by searching features for this slug
                const tree = await server.client.getTree();
                const feature = tree.find(f => f.slug === slug);
                if (!feature) {
                    vscode.window.showInformationMessage(`codoc: feature '${slug}' not found`);
                    return;
                }
                const bindings = await server.client.getFeatureBindings(feature.uuid);
                if (!bindings.length) {
                    vscode.window.showInformationMessage(`codoc: no bindings for '${slug}'`);
                    return;
                }
                const items = bindings.map(b => ({
                    label: b.anchor.file,
                    description: b.anchor.symbol_path ?? b.anchor.ts_query ?? '',
                    binding: b,
                }));
                const chosen = await vscode.window.showQuickPick(items, {
                    placeHolder: `Bindings for ${slug}`,
                });
                if (!chosen) return;
                const fileUri = vscode.Uri.file(path.join(server.rootDir!, chosen.binding.anchor.file));
                const bindDoc = await vscode.workspace.openTextDocument(fileUri);
                await vscode.window.showTextDocument(bindDoc);
            } catch (e: unknown) {
                const msg = e instanceof Error ? e.message : String(e);
                vscode.window.showErrorMessage(`codoc: failed to load bindings: ${msg}`);
            }
        }),
    );

    // ── Code lens on Python/TypeScript/JavaScript files ──────────────────────
    const codeLens = new CodocCodeLensProvider(server);
    context.subscriptions.push(
        vscode.languages.registerCodeLensProvider(
            [{ language: 'python' }, { language: 'typescript' }, { language: 'javascript' }],
            codeLens,
        ),
    );

    // ── Codoc language providers ─────────────────────────────────────────────
    const codocSelector: vscode.DocumentSelector = { language: 'codoc' };
    context.subscriptions.push(
        vscode.languages.registerFoldingRangeProvider(codocSelector, new CodocFoldingProvider()),
        vscode.languages.registerDocumentSymbolProvider(codocSelector, new CodocSymbolProvider()),
        vscode.languages.registerHoverProvider(codocSelector, new CodocHoverProvider(server)),
        vscode.languages.registerCodeLensProvider(codocSelector, new CodocCodocCodeLensProvider(server)),
        vscode.languages.registerDefinitionProvider(codocSelector, new CodocDefinitionProvider(server)),
        vscode.languages.registerCodeActionsProvider(
            codocSelector,
            new CodocCodeActionProvider(),
            { providedCodeActionKinds: [vscode.CodeActionKind.QuickFix] },
        ),
    );

    // ── Conflict resolver URI scheme ─────────────────────────────────────────
    const conflictProvider = new CodocConflictProvider();
    context.subscriptions.push(
        vscode.workspace.registerTextDocumentContentProvider('codoc-conflict', conflictProvider),
        conflictProvider,
    );

    // ── Decorations ──────────────────────────────────────────────────────────
    const decorations = createDecorations(context);
    const refreshDecorations = (editor?: vscode.TextEditor) => {
        const ed = editor ?? vscode.window.activeTextEditor;
        if (ed) applyDecorations(ed, decorations);
    };
    refreshDecorations();
    context.subscriptions.push(
        vscode.window.onDidChangeActiveTextEditor(refreshDecorations),
        vscode.workspace.onDidChangeTextDocument(e => {
            const ed = vscode.window.activeTextEditor;
            if (ed && ed.document === e.document) {
                lineTracker.onDocumentChange(e);
                refreshDecorations(ed);
            }
        }),
    );

    // ── On-save sync (debounced) ─────────────────────────────────────────────
    context.subscriptions.push(
        vscode.workspace.onDidSaveTextDocument(doc => {
            if (doc.languageId === 'codoc') {
                scheduleSyncCodocFile(doc, server, diagnostics);
            }
        }),
    );

    // ── Proposal count: refresh on connect, then every 30s ───────────────────
    server.onReady(() => refreshProposalCount());
    // Proposal count is now also refreshed via SSE reflect_done/accept/reject events.
    // Keep a 60s fallback poll instead of 30s.
    const countInterval = setInterval(refreshProposalCount, 60_000);
    context.subscriptions.push({ dispose: () => clearInterval(countInterval) });

    // Live activity tracker — shows Claude Code activity in gutter + status bar.
    const liveActivity = new LiveActivityTracker(context);
    server.onActivity((data) => liveActivity.handleEvent(data));
}

export function deactivate(): void {
    // Nothing to dispose — all disposables are registered on context.subscriptions.
}
