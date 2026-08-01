import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import * as cp from 'node:child_process';
import { WorkspaceState } from './state/workspace-state';
import { CodocCodeLensProvider } from './providers/code-lens';
import { CodocTreeLensProvider } from './providers/codoc-tree-lens';
import { CodocCodeActionProvider } from './providers/code-actions';
import { CodocCompletionProvider } from './providers/completion';
import { CodocDocumentLinkProvider } from './providers/doc-links';
import { CodocHoverProvider } from './providers/hover';
import { CodocInlayHintsProvider } from './providers/inlay';
import { CodocFoldingProvider } from './providers/folding';
import { CodocSymbolProvider } from './providers/symbol';
import { applyDecorations, applyPendingCodeDecorations, createDecorations } from './providers/decoration';
import { BridgeController } from './providers/bridge-controller';
import { BridgeCodeLensProvider } from './providers/bridge-lens';
import { subtreeTitleLines, siblingTitleLine, parentTitleLine, firstChildTitleLine } from './providers/feature-lines';
import { bindingsForFeature } from './state/bindings-model';
import { symbolLeaf } from './state/registry-model';
import { DependencyFocus } from './providers/focus';
import { AgentGutter } from './providers/agent';
import { CodocFileDecorationProvider } from './providers/file-decoration';
import { CodocTreeEditorProvider } from './providers/tree-editor';
import {
    ensureUv, provisionCodoc, cachedExecutables,
    WorkspaceUntrustedError, ProvisionCancelledError,
} from './setup/provision';
import { bootstrapCredentials, syncCredentialsToEnv, SECRET_OPENAI_KEY } from './setup/credentials';
import { startDaemon, stopDaemon, reapStaleLock } from './daemon/daemon-manager';
import { SETUP_STEPS, needsSetup } from './setup/setup-flow';
import { DEFAULT_HUB_PORT, hubUrl, serveCommandLine } from './serve/serve-manager';

/** Shared "codoc" OutputChannel — same name provision.ts / credentials.ts / daemon-manager.ts use. */
let _channel: vscode.OutputChannel | undefined;
function outputChannel(): vscode.OutputChannel {
    if (!_channel) _channel = vscode.window.createOutputChannel('codoc');
    return _channel;
}

/** Once-per-session guard so the first-run "Set up codoc" nudge isn't shown on every reload. */
let _setupOffered = false;

/**
 * The setup workspace root: a `.codoc/`-bearing root if one is already known
 * (re-run / repair), else the first workspace folder (the fresh-repo case where
 * `.codoc/` doesn't exist yet). Returns `undefined` when there is no folder open.
 */
function setupRootDir(state: WorkspaceState): string | undefined {
    return state.rootDir ?? vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

/**
 * Run a `codoc <subcommand>` console-script as a child process, streaming
 * stdout/stderr to the shared OutputChannel. Resolves on exit 0, rejects on a
 * non-zero exit or spawn error. Used for the long `codoc init` step (indexing +
 * LLM bootstrap), wrapped by the caller in `withProgress`.
 *
 * Argv-only (shell:false) with an explicit cwd/env; no untrusted input on argv.
 */
function runCodoc(codocPath: string, args: readonly string[], rootDir: string): Promise<void> {
    const channel = outputChannel();
    channel.appendLine(`$ ${codocPath} ${args.join(' ')}`);
    return new Promise<void>((resolve, reject) => {
        const child = cp.spawn(codocPath, args as string[], {
            cwd: rootDir,
            env: { ...process.env },
            shell: false,
        });
        child.stdout?.on('data', (buf: Buffer) => channel.append(buf.toString()));
        child.stderr?.on('data', (buf: Buffer) => channel.append(buf.toString()));
        child.on('error', err => reject(err));
        child.on('close', code => {
            if (code === 0) resolve();
            else reject(new Error(`codoc ${args[0] ?? ''} failed (exit ${code}). See the codoc output channel.`));
        });
    });
}

/**
 * Orchestrate the whole zero-manual-step setup, in the canonical order defined by
 * {@link SETUP_STEPS}: ensureUv → provisionCodoc → bootstrapCredentials → `codoc
 * init` → startDaemon. Credentials are written to `.env` BEFORE `codoc init` so
 * init's LLM bootstrap (indexing + tree proposal) has a configured provider — the
 * correctness invariant.
 *
 * Trust-gated (KTD6/R5): if the workspace is untrusted, prompts to trust it and
 * returns without spawning anything. Failures surface a Retry / View Log dialog.
 * On success sets the `codoc.ready` context key (drives the walkthrough completion).
 */
async function runSetup(context: vscode.ExtensionContext, state: WorkspaceState): Promise<void> {
    // KTD6/R5: gate every spawn/install on Workspace Trust.
    if (!vscode.workspace.isTrusted) {
        const choice = await vscode.window.showWarningMessage(
            'codoc setup installs and runs a Python core, so it needs a trusted workspace.',
            'Trust Workspace',
        );
        if (choice === 'Trust Workspace') {
            await vscode.commands.executeCommand('workbench.trust.manage');
        }
        return;
    }

    const rootDir = setupRootDir(state);
    if (!rootDir) {
        void vscode.window.showInformationMessage('Open a folder before setting up codoc.');
        return;
    }

    const channel = outputChannel();
    state.setProvisioning(true); // status bar → "$(cloud-download) setting up…"
    try {
        // 1. ensure-uv → 2. provision (each cancellable inside provision.ts).
        channel.appendLine(`codoc: ${SETUP_STEPS[0].label}`);
        const uvPath = await ensureUv();
        channel.appendLine(`codoc: ${SETUP_STEPS[1].label}`);
        const execs = await provisionCodoc(context, uvPath);

        // 3. credentials — MUST precede init (init runs the LLM bootstrap).
        channel.appendLine(`codoc: ${SETUP_STEPS[2].label}`);
        await bootstrapCredentials(context, rootDir);

        // 4. codoc init — long (indexing + LLM bootstrap); streamed to the channel.
        channel.appendLine(`codoc: ${SETUP_STEPS[3].label}`);
        await vscode.window.withProgress(
            { location: vscode.ProgressLocation.Notification, title: 'codoc: indexing your repo and proposing the feature tree…' },
            () => runCodoc(execs.codoc, ['init', '--root', rootDir], rootDir),
        );

        // 5. start the managed daemon (reap a stale lock first).
        channel.appendLine(`codoc: ${SETUP_STEPS[4].label}`);
        reapStaleLock(rootDir);
        startDaemon(context, execs.codoc, rootDir);

        await vscode.commands.executeCommand('setContext', 'codoc.ready', true);
        void vscode.window.showInformationMessage('codoc is set up — open your feature tree to get started.', 'Open Tree')
            .then(c => { if (c === 'Open Tree') void vscode.commands.executeCommand('codoc.open'); });
    } catch (err) {
        if (err instanceof ProvisionCancelledError) {
            channel.appendLine('codoc: setup cancelled.');
            return; // user-initiated; no error dialog
        }
        const msg = err instanceof WorkspaceUntrustedError
            ? err.message
            : `codoc setup failed: ${(err as Error).message}`;
        channel.appendLine(`codoc: ${msg}`);
        const choice = await vscode.window.showErrorMessage(msg, 'Retry', 'View Log');
        if (choice === 'Retry') {
            await runSetup(context, state);
        } else if (choice === 'View Log') {
            channel.show();
        }
    } finally {
        state.setProvisioning(false); // clear the "setting up…" status-bar state
    }
}

export function activate(context: vscode.ExtensionContext): void {
    const state = new WorkspaceState(context);
    const codocSelector: vscode.DocumentSelector = { language: 'codoc' };

    // ── codoc.setup — one-click provision → init → daemon ─────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.setup', () => runSetup(context, state)),
        // codoc.repair is an alias — re-running setup repairs a broken/partial state.
        vscode.commands.registerCommand('codoc.repair', () => runSetup(context, state)),
    );

    // ── URI handler: vscode://codoc.codoc/setup → codoc.setup ─────────────────
    // Validate the path; NEVER forward URI query params into any shell/spawn.
    context.subscriptions.push(
        vscode.window.registerUriHandler({
            handleUri(uri: vscode.Uri): void {
                if (uri.path === '/setup') {
                    void vscode.commands.executeCommand('codoc.setup');
                }
            },
        }),
    );

    // ── Managed daemon lifecycle ──────────────────────────────────────────────
    // On activate: if trusted + initialized + provisioned, reap a stale lock then
    // start the warm daemon. The user never runs `codoc watch` by hand (KTD3).
    const maybeStartDaemon = (): void => {
        if (!vscode.workspace.isTrusted) return;
        const rootDir = state.rootDir;
        if (!rootDir) return;
        const execs = cachedExecutables(context);
        if (!execs) return;
        reapStaleLock(rootDir);
        startDaemon(context, execs.codoc, rootDir);
    };
    maybeStartDaemon();
    // Trust may be granted after activation — start the daemon then.
    context.subscriptions.push(
        vscode.workspace.onDidGrantWorkspaceTrust(() => maybeStartDaemon()),
    );

    // ── Secrets: OpenAI key change → re-mirror .env + restart the daemon ───────
    context.subscriptions.push(
        context.secrets.onDidChange(e => {
            if (e.key !== SECRET_OPENAI_KEY) return;
            const rootDir = state.rootDir;
            if (!rootDir) return;
            void syncCredentialsToEnv(context, rootDir).then(() => {
                // Bounce the daemon so the new key (in .env) takes effect.
                const execs = cachedExecutables(context);
                if (vscode.workspace.isTrusted && execs) {
                    stopDaemon();
                    reapStaleLock(rootDir);
                    startDaemon(context, execs.codoc, rootDir);
                }
            });
        }),
    );

    // ── First-run nudge: no .codoc/ + nothing provisioned → offer setup once ──
    if (needsSetup(state.rootDir !== null, cachedExecutables(context) !== undefined) && !_setupOffered) {
        _setupOffered = true;
        void vscode.commands.executeCommand(
            'workbench.action.openWalkthrough', 'codoc.codoc#codocSetup', false,
        );
        void vscode.window.showInformationMessage('Set up codoc to navigate your codebase as a feature tree.', 'Set up codoc')
            .then(c => { if (c === 'Set up codoc') void vscode.commands.executeCommand('codoc.setup'); });
    }

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

    // ── codoc serve — the deployed hub (page deployment + multi-user editing) ──
    // The hub is a SEPARATE process (peer to the extension): it supervises the
    // daemon and serves the same intent-tree editor as a web app — localhost by
    // default; remote contributors reach it over a tunnel + GitHub App. The
    // extension just makes it easy to START and SHARE; it never owns the hub's
    // lifecycle (the hub owns the daemon, and the local daemon-manager defers to
    // it). We launch it in a dedicated integrated terminal so the user sees the
    // live log and can Ctrl-C to stop. Pure argv/URL logic is in serve-manager.ts.
    let _hubTerminal: vscode.Terminal | undefined;
    const startHub = async (tunnel: boolean): Promise<void> => {
        const root = state.rootDir ?? setupRootDir(state);
        if (!root) { void vscode.window.showInformationMessage('No codoc tree found. Run `codoc init` first.'); return; }
        // Same Workspace-Trust gate the daemon spawn uses (KTD6): the hub runs a
        // server + can realize code, so never auto-launch in an untrusted folder.
        if (!vscode.workspace.isTrusted) {
            void vscode.window.showWarningMessage('codoc serve needs a trusted workspace.');
            return;
        }
        // The standalone SPA the hub serves ships inside the extension (assembled
        // into dist/webview by esbuild). Pointing --static-dir here makes the hub
        // serve the REAL editor instead of the placeholder page.
        const staticDir = path.join(context.extensionPath, 'dist', 'webview');
        const cmd = serveCommandLine({
            root, port: DEFAULT_HUB_PORT,
            staticDir: fs.existsSync(staticDir) ? staticDir : undefined,
            tunnel,
        });
        _hubTerminal?.dispose();
        _hubTerminal = vscode.window.createTerminal({ name: 'codoc hub', cwd: root });
        _hubTerminal.show();
        _hubTerminal.sendText(cmd);

        const url = hubUrl(DEFAULT_HUB_PORT);
        const open = 'Open in browser', copy = 'Copy link', remote = 'Remote access…';
        const msg = tunnel
            ? `codoc hub starting with a tunnel — gate it with the GitHub App / Cloudflare Access (see the deploy doc). Local: ${url}`
            : `codoc hub starting at ${url} — anyone on this machine can edit the tree. For remote contributors, set up the tunnel + GitHub App.`;
        const pick = await vscode.window.showInformationMessage(msg, open, copy, remote);
        if (pick === open) void vscode.env.openExternal(vscode.Uri.parse(url));
        else if (pick === copy) await vscode.env.clipboard.writeText(url);
        else if (pick === remote) void vscode.env.openExternal(vscode.Uri.parse(
            'https://github.com/ryanyen2/CodeNav/blob/main/docs/serve-deployment.md'));
    };
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.serve.start', () => startHub(false)),
        vscode.commands.registerCommand('codoc.serve.startTunnel', () => startHub(true)),
        vscode.commands.registerCommand('codoc.serve.stop', () => {
            if (!_hubTerminal) { void vscode.window.showInformationMessage('codoc hub is not running from this window.'); return; }
            _hubTerminal.dispose();   // SIGINT → uvicorn shutdown → supervisor.stop()
            _hubTerminal = undefined;
        }),
        { dispose: () => _hubTerminal?.dispose() },
    );

    // A discoverable status-bar affordance for sharing (like a "Go Live" button).
    const shareItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 50);
    shareItem.text = '$(broadcast) Share';
    shareItem.tooltip = 'codoc: start the deployed hub (browser editor for collaborators)';
    shareItem.command = 'codoc.serve.start';
    if (state.rootDir) shareItem.show();
    context.subscriptions.push(
        shareItem,
        state.onDidChange(() => { if (state.rootDir) shareItem.show(); else shareItem.hide(); }),
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
            // Containment guard: a `codoc:` ref is authored text (and, via the hub, can be
            // teammate/remote-authored), so a crafted `codoc:../../../../etc/passwd` must
            // not open an arbitrary file outside the repo. Resolve and require the target
            // to stay under rootDir before opening.
            const rootResolved = path.resolve(state.rootDir);
            const target = path.resolve(rootResolved, file);
            const rel = path.relative(rootResolved, target);
            if (rel.startsWith('..') || path.isAbsolute(rel)) {
                void vscode.window.showWarningMessage(`codoc: refusing to open ${file} — outside the workspace.`);
                return;
            }
            const uri = vscode.Uri.file(target);
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
                    // symbol may be "file::Qualified.Name" format; extract the leaf
                    // (strip the `file::` qualifier, then the last `.`-segment).
                    const leaf = symbolLeaf(symbol);
                    const found = findSymbolByName(syms, leaf);
                    if (found) targetRange = found.selectionRange;
                }
            } catch { /* fall through to regex */ }

            // Fallback: regex scan
            if (!targetRange) {
                const leaf = symbolLeaf(symbol);
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

    // ── codoc.pickBinding — chip click: quick-pick a binding to open ──────────
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.pickBinding', async (featureId: string) => {
            const binds = bindingsForFeature(state.sidecar, featureId);
            if (binds.length === 0) return;
            if (binds.length === 1) {
                await vscode.commands.executeCommand('codoc.openRef', binds[0].file, binds[0].symbol);
                return;
            }
            const picked = await vscode.window.showQuickPick(
                binds.map(b => {
                    // DISPLAY variant (not canonical symbolLeaf): keep `Class.method`,
                    // map the synthetic `__module__` to the `‹module›` glyph.
                    const sym = b.symbol.split('::').pop() ?? b.symbol;
                    const tail = sym === '__module__' ? '‹module›' : sym;
                    return { label: `$(symbol-method) ${tail}`, description: b.file, b };
                }),
                { placeHolder: `${binds.length} bindings — pick one to open`, matchOnDescription: true },
            );
            if (picked) await vscode.commands.executeCommand('codoc.openRef', picked.b.file, picked.b.symbol);
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
        vscode.languages.registerHoverProvider(codocSelector, new CodocHoverProvider(state)),
        vscode.languages.registerFoldingRangeProvider(codocSelector, new CodocFoldingProvider()),
        vscode.languages.registerDocumentSymbolProvider(codocSelector, new CodocSymbolProvider()),
    );

    // ── Decorations (hide ids, colour diff hunks, strike retired) ─────────────
    const decorations = createDecorations(context);
    const refreshDecorations = (editor?: vscode.TextEditor): void => {
        const ed = editor ?? vscode.window.activeTextEditor;
        if (!ed) return;
        applyDecorations(ed, decorations, state.activeFeatureLines, state.sidecar, state.registry);
        // Reverse direction: mark the code a queued tree edit will rework.
        const rel = vscode.workspace.asRelativePath(ed.document.fileName);
        applyPendingCodeDecorations(ed, decorations, state.pendingCodeForFile(rel));
    };
    refreshDecorations();

    // ── Live cross-surface bridge (P2 / §A) — doc→code green highlight + live lens ──
    const bridge = new BridgeController(context, state, decorations);
    context.subscriptions.push(
        vscode.languages.registerCodeLensProvider(
            [{ language: 'python' }, { language: 'typescript' }, { language: 'javascript' }],
            new BridgeCodeLensProvider(state, bridge),
        ),
        // §A.6 (P2 fix 3 / §6 hardening): the bridge remembers a dismissal only on a true TAB
        // close, not a tab switch — so listen to BOTH visible-editor changes (repaint) and tab
        // close/open (the dismissal signal). The `bridge.rearm` command re-enables auto-open.
        vscode.window.onDidChangeVisibleTextEditors(() => bridge.noteVisibleEditorsChanged()),
        vscode.window.tabGroups.onDidChangeTabs(() => bridge.noteVisibleEditorsChanged()),
        vscode.commands.registerCommand('codoc.bridge.rearm', () => bridge.rearm()),
        { dispose: () => bridge.dispose() },
    );

    context.subscriptions.push(
        vscode.window.onDidChangeActiveTextEditor(refreshDecorations),
        vscode.workspace.onDidChangeTextDocument(e => {
            const ed = vscode.window.activeTextEditor;
            if (ed && ed.document === e.document) refreshDecorations(ed);
        }),
        // Sidecar / realize.md reload must repaint overlays + pending-code marks
        // across every visible editor (the changed file may not be active) + the bridge.
        state.onDidChange(() => { vscode.window.visibleTextEditors.forEach(refreshDecorations); bridge.repaint(); }),
    );

    // ── Dependency focus (opacity dimming on cursor) ───────────────────────────
    const focusController = new DependencyFocus(state, decorations, context);

    // ── Agent gutter pulse ────────────────────────────────────────────────────
    const agentGutter = new AgentGutter(state, context);

    // ── File decoration provider (Explorer badges) ────────────────────────────
    const fileDecProvider = new CodocFileDecorationProvider(state);
    context.subscriptions.push(vscode.window.registerFileDecorationProvider(fileDecProvider));

    // ── Custom GUI editor for tree.codoc (opens by default; ⇄ text drops out) ─
    context.subscriptions.push(
        vscode.window.registerCustomEditorProvider(
            CodocTreeEditorProvider.viewType,
            new CodocTreeEditorProvider(context, state, bridge),
            { webviewOptions: { retainContextWhenHidden: true }, supportsMultipleEditorsPerDocument: false },
        ),
    );

    state.onDidChange(() => {
        refreshDecorations();
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
    // Synchronously SIGTERM the managed daemon (deactivate has a limited budget,
    // can't await) — the Python parent-death self-exit is the backstop if this
    // never runs (host crash).
    stopDaemon();
}
