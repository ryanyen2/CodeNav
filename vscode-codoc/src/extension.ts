import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { ServerState } from './state/server';
import { CodocCodeLensProvider } from './providers/code-lens';
import { CodocFoldingProvider } from './providers/folding';
import { CodocSymbolProvider } from './providers/symbol';
import { CodocHoverProvider } from './providers/hover';
import { CodocCodocCodeLensProvider } from './providers/codelens';
import { CodocDefinitionProvider } from './providers/definition';
import { CodocCodeActionProvider } from './providers/code-actions';
import { CodocCompletionProvider } from './providers/completion';
import { applyDecorations, createDecorations } from './providers/decoration';
import { scheduleSyncCodocFile, onSaveCodocFile } from './sync-on-save';
import { LiveActivityTracker } from './state/live-activity';

export function activate(context: vscode.ExtensionContext): void {
    const server = new ServerState(context);
    const diagnostics = vscode.languages.createDiagnosticCollection('codoc');
    context.subscriptions.push(diagnostics);

    // Refresh the status bar by pulling /state (pending count, stage, etc.)
    async function refreshState(): Promise<void> {
        await server.refreshState();
    }

    // ── codoc.sync — state-aware one-stop command ────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.sync', async () => {
            if (!server.client) {
                vscode.window.showInformationMessage(
                    'codoc server not reachable — run `codoc server --port 8001` in a terminal first.',
                );
                return;
            }
            let result;
            try {
                result = await vscode.window.withProgress(
                    {
                        location: vscode.ProgressLocation.Notification,
                        title: 'codoc: syncing...',
                        cancellable: false,
                    },
                    () => server.client!.syncRepo(),
                );
            } catch (e: unknown) {
                const msg = e instanceof Error ? e.message : String(e);
                vscode.window.showErrorMessage(`codoc sync failed: ${msg}`);
                return;
            }

            await refreshState();

            const after = result.stage_after;
            if (after === 'proposals-pending' || after === 'bootstrap-review') {
                const open = await vscode.window.showInformationMessage(
                    `codoc: ${result.pending_count} proposal(s) ready for review`,
                    'Open Tree',
                );
                if (open === 'Open Tree') await vscode.commands.executeCommand('codoc.open');
            } else if (after === 'clean' && result.actions.length > 0) {
                vscode.window.showInformationMessage(`codoc: ${result.summary}`);
                await vscode.commands.executeCommand('codoc.open');
            } else if (after === 'needs-bootstrap') {
                vscode.window.showInformationMessage('codoc: set OPENAI_API_KEY and re-run sync to bootstrap');
            }
        }),
    );

    // ── codoc.open — open the rendered tree file ─────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.open', async () => {
            if (!server.rootDir) {
                const confirm = await vscode.window.showInformationMessage(
                    'No codoc tree found in this workspace. Run `codoc sync` from the terminal to initialize.',
                    'Run codoc.sync',
                );
                if (confirm === 'Run codoc.sync') await vscode.commands.executeCommand('codoc.sync');
                return;
            }

            const indexPath = path.join(server.rootDir, '.codoc', 'tree', '_index.codoc');
            if (!fs.existsSync(indexPath)) {
                // Tree not rendered yet — run sync first.
                await vscode.commands.executeCommand('codoc.sync');
                return;
            }

            const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(indexPath));
            await vscode.window.showTextDocument(doc);
            await refreshState();
        }),
    );

    // ── codoc.renderHard — force re-render the tree ──────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.renderHard', async () => {
            if (!server.client) return;
            await vscode.window.withProgress(
                {
                    location: vscode.ProgressLocation.Notification,
                    title: 'codoc: Rendering...',
                    cancellable: false,
                },
                () => server.client!.renderTree(),
            );
            await refreshState();
            if (!server.rootDir) return;
            const indexPath = path.join(server.rootDir, '.codoc', 'tree', '_index.codoc');
            if (fs.existsSync(indexPath)) {
                const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(indexPath));
                await vscode.window.showTextDocument(doc);
            }
        }),
    );

    // ── Proposal commands ────────────────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.acceptProposal', async (_uri: vscode.Uri | undefined, hlc: string) => {
            if (!server.client || !hlc) return;
            try {
                await server.client.acceptProposal(hlc);
                await server.client.renderTree();
                await refreshState();
            } catch (e: unknown) {
                vscode.window.showErrorMessage(`codoc accept failed: ${e instanceof Error ? e.message : String(e)}`);
            }
        }),
        vscode.commands.registerCommand('codoc.rejectProposal', async (_uri: vscode.Uri | undefined, hlc: string) => {
            if (!server.client || !hlc) return;
            try {
                await server.client.rejectProposal(hlc);
                await server.client.renderTree();
                await refreshState();
            } catch (e: unknown) {
                vscode.window.showErrorMessage(`codoc reject failed: ${e instanceof Error ? e.message : String(e)}`);
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
            if (newSlug === undefined) return;

            const edits: Record<string, unknown> = {};
            if (newSlug.trim() && newSlug.trim() !== proposedSlug) {
                edits.slug = newSlug.trim();
            }
            try {
                await server.client.acceptProposal(hlc, Object.keys(edits).length ? edits : undefined);
                await server.client.renderTree();
                await refreshState();
            } catch (e: unknown) {
                vscode.window.showErrorMessage(`codoc accept failed: ${e instanceof Error ? e.message : String(e)}`);
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
                await refreshState();
            } catch (e: unknown) {
                vscode.window.showErrorMessage(`codoc accept-all failed: ${e instanceof Error ? e.message : String(e)}`);
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
                await refreshState();
            } catch (e: unknown) {
                vscode.window.showErrorMessage(`codoc reject-all failed: ${e instanceof Error ? e.message : String(e)}`);
            }
        }),
    );

    // ── Proposal actions widget (status bar click dispatches here) ───────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.showProposalActions', async () => {
            const state = server.repoState;
            const items: vscode.QuickPickItem[] = [
                { label: '$(file) Open Tree', description: 'Open _index.codoc' },
                { label: '$(check) Accept All', description: 'Accept all pending proposals' },
                { label: '$(x) Reject All', description: 'Reject all pending proposals' },
            ];
            if (state) {
                items.unshift({ label: `$(info) Stage: ${state.stage}`, description: state.next_action, kind: vscode.QuickPickItemKind.Separator });
            }
            const choice = await vscode.window.showQuickPick(items.filter(i => i.kind !== vscode.QuickPickItemKind.Separator), { title: 'codoc proposals' });
            if (choice?.label.includes('Open Tree')) {
                await vscode.commands.executeCommand('codoc.open');
            } else if (choice?.label.includes('Accept All')) {
                await vscode.commands.executeCommand('codoc.acceptAll');
            } else if (choice?.label.includes('Reject All')) {
                await vscode.commands.executeCommand('codoc.rejectAll');
            }
        }),
    );

    // ── Per-line proposal quick-fixes ────────────────────────────────────────
    const HLC_RE = /#\s*\?([0-9a-zA-Z:\-_]+)/;

    function hlcForLine(document: vscode.TextDocument, lineNum: number): string | null {
        const line = document.lineAt(lineNum).text;
        const m = HLC_RE.exec(line);
        if (m) return m[1];

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
        } catch { /* meta file unreadable */ }
        return null;
    }

    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.acceptProposalAtLine', async (lineNum: number) => {
            if (!server.client) return;
            const editor = vscode.window.activeTextEditor;
            if (!editor) return;
            const hlc = hlcForLine(editor.document, lineNum);
            if (!hlc) { vscode.window.showWarningMessage('codoc: no HLC found on this line'); return; }
            await vscode.commands.executeCommand('codoc.acceptProposal', editor.document.uri, hlc);
        }),
        vscode.commands.registerCommand('codoc.rejectProposalAtLine', async (lineNum: number) => {
            if (!server.client) return;
            const editor = vscode.window.activeTextEditor;
            if (!editor) return;
            const hlc = hlcForLine(editor.document, lineNum);
            if (!hlc) { vscode.window.showWarningMessage('codoc: no HLC found on this line'); return; }
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
            for (let i = 0; i < doc.lineCount; i++) {
                if (doc.lineAt(i).text.includes(slugOrTitle)) {
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
                const tree = await server.client.getTree();
                const feature = tree.find(f => f.slug === slug);
                if (!feature) { vscode.window.showInformationMessage(`codoc: feature '${slug}' not found`); return; }
                const bindings = await server.client.getFeatureBindings(feature.uuid);
                if (!bindings.length) { vscode.window.showInformationMessage(`codoc: no bindings for '${slug}'`); return; }
                const items = bindings.map(b => ({
                    label: b.anchor.file,
                    description: b.anchor.symbol_path ?? b.anchor.ts_query ?? '',
                    binding: b,
                }));
                const chosen = await vscode.window.showQuickPick(items, { placeHolder: `Bindings for ${slug}` });
                if (!chosen) return;
                const fileUri = vscode.Uri.file(path.join(server.rootDir!, chosen.binding.anchor.file));
                await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(fileUri));
            } catch (e: unknown) {
                vscode.window.showErrorMessage(`codoc: failed to load bindings: ${e instanceof Error ? e.message : String(e)}`);
            }
        }),
    );

    // ── Code lens on Python/TypeScript/JavaScript files ──────────────────────
    context.subscriptions.push(
        vscode.languages.registerCodeLensProvider(
            [{ language: 'python' }, { language: 'typescript' }, { language: 'javascript' }],
            new CodocCodeLensProvider(server),
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
        vscode.languages.registerCompletionItemProvider(
            codocSelector,
            new CodocCompletionProvider(server),
            '@',
        ),
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
            if (ed && ed.document === e.document) refreshDecorations(ed);
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

    // ── FileSystemWatcher — keep status bar fresh when server re-renders ─────
    // The render_token in tree.meta.json changes on every server-side render.
    // We use it as an echo guard: if the token matches what we last saw, this
    // change was triggered by our own save and we skip reprocessing.
    let lastSeenRenderToken = '';
    const treeWatcher = vscode.workspace.createFileSystemWatcher('**/.codoc/tree/**/*.codoc');
    const onTreeFileChange = async () => {
        if (!server.rootDir) return;
        const metaPath = path.join(server.rootDir, '.codoc', 'tree', 'tree.meta.json');
        try {
            const meta = JSON.parse(fs.readFileSync(metaPath, 'utf-8')) as { render_token?: string };
            const token = meta.render_token ?? '';
            if (token && token === lastSeenRenderToken) return; // our own save — skip
            lastSeenRenderToken = token;
        } catch { /* meta unreadable — proceed anyway */ }
        await refreshState();
        refreshDecorations();
    };
    context.subscriptions.push(
        treeWatcher,
        treeWatcher.onDidChange(onTreeFileChange),
        treeWatcher.onDidCreate(onTreeFileChange),
    );

    // ── State refresh: on connect and SSE-driven ─────────────────────────────
    server.onReady(() => void refreshState());

    // Live activity tracker — Claude Code gutter pulse + status bar entry.
    const liveActivity = new LiveActivityTracker(context);
    server.onActivity((data) => liveActivity.handleEvent(data));
}

export function deactivate(): void {
    // All disposables registered on context.subscriptions.
}
