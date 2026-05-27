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
import { subtreeTitleLines, siblingTitleLine, parentTitleLine, firstChildTitleLine } from './providers/feature-lines';
import { bindingsForFeature } from './state/bindings-model';
import { DependencyFocus } from './providers/focus';
import { AgentGutter } from './providers/agent';
import { CodocFileDecorationProvider } from './providers/file-decoration';

export function activate(context: vscode.ExtensionContext): void {
    const state = new WorkspaceState(context);
    const codocSelector: vscode.DocumentSelector = { language: 'codoc' };

    // ── Feature tree panel ───────────────────────────────────────────────────
    const featureTreeProvider = new FeatureTreeProvider(state);
    const treeView = vscode.window.createTreeView('codoc.featureTree', { treeDataProvider: featureTreeProvider, showCollapseAll: true });
    context.subscriptions.push(
        treeView,
        vscode.commands.registerCommand('codoc.refreshFeatureTree', () => featureTreeProvider.refresh()),
        // Sync sidebar selection to the cursor in tree.codoc.
        vscode.window.onDidChangeTextEditorSelection(e => {
            if (e.textEditor.document.languageId !== 'codoc') return;
            const line = e.selections[0]?.active.line;
            if (line === undefined) return;
            const features = state.features;
            // Find the feature at/above cursor.
            let best: typeof features[0] | undefined;
            for (const f of features) {
                if (f.line <= line && (!best || f.line > best.line)) best = f;
            }
            if (best) {
                const item = featureTreeProvider.itemForId(best.id ?? '');
                if (item) treeView.reveal(item, { select: true, focus: false }).then(undefined, () => {});
            }
        }),
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
    const verdict = async (ids: string[] | string, accept: boolean): Promise<void> => {
        state.writeVerdict(Array.isArray(ids) ? ids : [ids], accept);
        vscode.window.setStatusBarMessage('$(sync~spin) codoc: applying…', 3000);
    };
    const bulkVerdict = async (ids: string[], accept: boolean): Promise<void> => {
        if (!ids || ids.length === 0) return;
        const label = accept ? 'Accept all' : 'Reject all';
        const choice = await vscode.window.showWarningMessage(
            `${label} ${ids.length} proposed change${ids.length === 1 ? '' : 's'}?`,
            { modal: true }, label);
        if (choice !== label) return;
        await verdict(ids, accept);
    };
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.acceptProposal', async (id: string) => verdict(id, true)),
        vscode.commands.registerCommand('codoc.rejectProposal', async (id: string) => verdict(id, false)),
        vscode.commands.registerCommand('codoc.acceptAll', async (ids: string[]) => bulkVerdict(ids, true)),
        vscode.commands.registerCommand('codoc.rejectAll', async (ids: string[]) => bulkVerdict(ids, false)),
    );

    // ── codoc.openRef — jump from an inline [..](codoc:file#symbol) to code ───
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.openRef', async (file: string, symbol: string) => {
            if (!state.rootDir) return;
            const uri = vscode.Uri.file(path.join(state.rootDir, file));
            let doc: vscode.TextDocument;
            let targetEditor: vscode.TextEditor;
            try {
                doc = await vscode.workspace.openTextDocument(uri);
                // Open Beside, preserve focus on tree.codoc.
                targetEditor = await vscode.window.showTextDocument(doc, {
                    viewColumn: vscode.ViewColumn.Beside,
                    preserveFocus: true,
                    preview: true,
                });
            } catch {
                void vscode.window.showWarningMessage(`codoc: couldn't open ${file} — the reference may be stale.`);
                return;
            }

            if (!symbol) return;

            let targetRange: vscode.Range | null = null;

            // Try VS Code's document symbol provider for precise range.
            try {
                const syms = await vscode.commands.executeCommand<vscode.DocumentSymbol[]>(
                    'vscode.executeDocumentSymbolProvider', uri
                );
                if (syms) {
                    // symbol may be "file::Qualified.Name" format; extract leaf after last '.' or '::'
                    const leaf = symbol.split('::').pop()?.split('.').pop() ?? symbol;
                    const found = findSymbolByName(syms, leaf);
                    if (found) targetRange = found.selectionRange;
                }
            } catch { /* fall through to regex */ }

            // Fallback: regex scan
            if (!targetRange) {
                const leaf = symbol.includes('::') ? symbol.split('::').pop()!.split('.').pop()!
                            : symbol.includes('.')  ? symbol.split('.').pop()!
                            : symbol;
                const re = new RegExp(`(?:def|class|function|const|let|var)\\s+${leaf}\\b|\\b${leaf}\\s*[=:(]`);
                for (let i = 0; i < doc.lineCount; i++) {
                    if (re.test(doc.lineAt(i).text)) {
                        targetRange = new vscode.Range(i, 0, i, doc.lineAt(i).text.length);
                        break;
                    }
                }
            }

            if (!targetRange) return;
            targetEditor.revealRange(targetRange, vscode.TextEditorRevealType.InCenter);

            // Flash highlight: apply then fade over 900ms.
            const flashDec = vscode.window.createTextEditorDecorationType({
                backgroundColor: new vscode.ThemeColor('editor.findMatchHighlightBackground'),
                isWholeLine: false,
            });
            targetEditor.setDecorations(flashDec, [targetRange]);
            setTimeout(() => { targetEditor.setDecorations(flashDec, []); flashDec.dispose(); }, 900);
        }),
    );

    // ── codoc.openFirstBinding — Alt+B: jump from tree node to its first bound symbol ──
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.openFirstBinding', async () => {
            const ed = vscode.window.activeTextEditor;
            if (!ed || ed.document.languageId !== 'codoc') return;
            const line = ed.selection.active.line;
            // Find the feature whose title is at or above the cursor.
            const features = state.features;
            let best: typeof features[0] | undefined;
            for (const f of features) {
                if (f.line <= line && (!best || f.line > best.line)) best = f;
            }
            if (!best?.id) {
                void vscode.window.showInformationMessage('No feature at cursor — position cursor on a feature title line.');
                return;
            }
            const binds = bindingsForFeature(state.sidecar, best.id);
            if (binds.length === 0) {
                void vscode.window.showInformationMessage(`"${best.title}" has no code bindings yet.`);
                return;
            }
            const b = binds[0];
            await vscode.commands.executeCommand('codoc.openRef', b.file, b.symbol);
        }),
    );

    // ── codoc.navigateToFeature ───────────────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.navigateToFeature', async (titleOrId: string | null) => {
            const treePath = state.rootDir && path.join(state.rootDir, '.codoc', 'tree.codoc');
            if (!treePath || !titleOrId || !fs.existsSync(treePath)) return;
            const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(treePath));
            const editor = await vscode.window.showTextDocument(doc);
            // Match by feature id ⟨f-id⟩ first, then fall back to title/id substring.
            const exactId = `⟨${titleOrId}⟩`;
            let targetLine = -1;
            for (let i = 0; i < doc.lineCount; i++) {
                if (doc.lineAt(i).text.includes(exactId)) { targetLine = i; break; }
            }
            if (targetLine < 0) {
                for (let i = 0; i < doc.lineCount; i++) {
                    if (doc.lineAt(i).text.includes(titleOrId)) { targetLine = i; break; }
                }
            }
            if (targetLine >= 0) {
                const pos = new vscode.Position(targetLine, 0);
                editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
                editor.selection = new vscode.Selection(pos, pos);
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

    // ── Tree keyboard navigation (Alt+Arrow, doesn't break text editing) ────────
    const navTo = (ed: vscode.TextEditor, target: number | null): void => {
        if (target === null) return;
        const pos = new vscode.Position(target, 0);
        ed.selection = new vscode.Selection(pos, pos);
        ed.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.Default);
    };
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.nav.nextSibling', () => {
            const ed = vscode.window.activeTextEditor;
            if (!isCodocEditor(ed)) return;
            navTo(ed, siblingTitleLine(ed.document, ed.selection.active.line, 'next'));
        }),
        vscode.commands.registerCommand('codoc.nav.prevSibling', () => {
            const ed = vscode.window.activeTextEditor;
            if (!isCodocEditor(ed)) return;
            navTo(ed, siblingTitleLine(ed.document, ed.selection.active.line, 'prev'));
        }),
        vscode.commands.registerCommand('codoc.nav.parent', () => {
            const ed = vscode.window.activeTextEditor;
            if (!isCodocEditor(ed)) return;
            navTo(ed, parentTitleLine(ed.document, ed.selection.active.line));
        }),
        vscode.commands.registerCommand('codoc.nav.firstChild', async () => {
            const ed = vscode.window.activeTextEditor;
            if (!isCodocEditor(ed)) return;
            const curLine = ed.selection.active.line;
            const target = firstChildTitleLine(ed.document, curLine);
            if (target !== null) {
                navTo(ed, target);
            } else {
                // No child — try to expand.
                await vscode.commands.executeCommand('editor.unfold', { selectionLines: [curLine] });
            }
        }),
    );

    // ── Hunk-at-cursor accept/reject (keyboard shortcuts alt+a / alt+r) ──────────
    const hunkVerdict = (accept: boolean): void => {
        const ed = vscode.window.activeTextEditor;
        if (!isCodocEditor(ed)) return;
        const line = ed.selection.active.line;
        const text = ed.document.lineAt(line).text;
        const eventIdMatch = /⟨(e-[0-9a-f]+)⟩/.exec(text);
        if (eventIdMatch) {
            state.writeVerdict([eventIdMatch[1]], accept);
        }
    };
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.acceptHunkAtCursor', () => hunkVerdict(true)),
        vscode.commands.registerCommand('codoc.rejectHunkAtCursor', () => hunkVerdict(false)),
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
        vscode.languages.registerCodeActionsProvider(codocSelector, new CodocCodeActionProvider(state),
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
        if (ed) applyDecorations(ed, decorations, state.activeFeatureLines, state.sidecar);
    };
    refreshDecorations();
    context.subscriptions.push(
        vscode.window.onDidChangeActiveTextEditor(refreshDecorations),
        vscode.workspace.onDidChangeTextDocument(e => {
            const ed = vscode.window.activeTextEditor;
            if (ed && ed.document === e.document) refreshDecorations(ed);
        }),
        // Sidecar reload (proposals / realized) must repaint the in-place overlays.
        state.onDidChange(() => refreshDecorations()),
    );

    // ── Dependency focus (opacity dimming on cursor) ───────────────────────────
    const focusController = new DependencyFocus(state, decorations, context);

    // ── Agent gutter pulse ────────────────────────────────────────────────────
    const agentGutter = new AgentGutter(state, context);

    // ── File decoration provider (Explorer badges) ────────────────────────────
    const fileDecProvider = new CodocFileDecorationProvider(state);
    context.subscriptions.push(vscode.window.registerFileDecorationProvider(fileDecProvider));

    state.onDidChange(() => {
        refreshDecorations();
        featureTreeProvider.refresh();
        focusController.refresh();
        agentGutter.update();
        fileDecProvider.update();
    });
}

/** Recursively find a document symbol by exact name. */
function findSymbolByName(symbols: vscode.DocumentSymbol[], name: string): vscode.DocumentSymbol | undefined {
    for (const s of symbols) {
        if (s.name === name) return s;
        const found = findSymbolByName(s.children, name);
        if (found) return found;
    }
    return undefined;
}

export function deactivate(): void {
    // All disposables registered on context.subscriptions.
}
