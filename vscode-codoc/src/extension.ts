/**
 * extension.ts — what the extension does when VS Code activates it.
 *
 * It registers the Codoc Tree editor (host/provider.ts), keeps a daemon
 * running for the repository, shows the daemon's status in the status bar,
 * and registers the commands in package.json. Setup installs the Python core,
 * picks a provider, builds the first tree and starts the daemon.
 *
 * The daemon is kept running by asking the same question repeatedly: if this
 * is a trusted, initialised, provisioned repository with no live daemon, start
 * one. `reconcileDaemon` asks on activation, on a timer, on a trust grant and
 * on every `.codoc` change, so a missed event costs at most one timer tick.
 *
 * The extension holds no model of the tree. The page reads the daemon's
 * projection and writes ops to the daemon's inbox.
 */
import * as cp from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as vscode from 'vscode';

import { resetDaemonBackoff, startDaemon, stopDaemon } from './host/daemon-manager';
import { output } from './host/output';
import { CodocEditorProvider } from './host/provider';
import { DEFAULT_HUB_PORT, serveArgs } from './host/serve-manager';
import { bootstrapCredentials } from './host/setup/credentials';
import { ProvisionCancelledError, WorkspaceUntrustedError, cachedExecutables, ensureUv, provisionCodoc } from './host/setup/provision';
import { commandLine } from './host/shell';
import { Workspace, statusBarView } from './host/workspace';

const DAEMON_RECONCILE_MS = 15_000;

/** Run a codoc command to completion, copying its output to the channel. */
function runCodoc(codocPath: string, args: readonly string[], rootDir: string): Promise<void> {
    return new Promise((resolve, reject) => {
        const child = cp.spawn(codocPath, args, { cwd: rootDir, env: { ...process.env, PYTHONUNBUFFERED: '1' } });
        child.stdout.on('data', d => output().append(String(d)));
        child.stderr.on('data', d => output().append(String(d)));
        child.on('error', reject);
        child.on('close', code => (code === 0 ? resolve() : reject(new Error(`codoc ${args[0]} exited with ${code}`))));
    });
}

/**
 * Setup, in the order it has to run: uv, the Python core, the provider, the
 * first tree, the daemon. The provider comes before the tree because building
 * the tree calls the model.
 */
async function runSetup(context: vscode.ExtensionContext, state: Workspace): Promise<void> {
    if (!vscode.workspace.isTrusted) {
        const choice = await vscode.window.showWarningMessage('codoc installs and runs a Python core, so it needs a trusted workspace.', 'Trust Workspace');
        if (choice === 'Trust Workspace') await vscode.commands.executeCommand('workbench.trust.manage');
        return;
    }
    const rootDir = state.rootDir;
    if (!rootDir) { void vscode.window.showInformationMessage('Open a folder before setting up codoc.'); return; }
    const log = output();
    state.setProvisioning(true);
    try {
        log.appendLine('codoc: checking for uv…');
        const uv = await ensureUv();
        log.appendLine('codoc: installing the codoc core…');
        const execs = await provisionCodoc(context, uv);
        log.appendLine('codoc: choosing the provider…');
        await bootstrapCredentials(rootDir);
        log.appendLine('codoc: indexing the repo and building the feature tree…');
        if (!state.initialized) {
            await vscode.window.withProgress(
                { location: vscode.ProgressLocation.Notification, title: 'codoc: indexing your repo and building the feature tree…' },
                () => runCodoc(execs.codoc, ['init', '--root', rootDir], rootDir),
            );
        }
        log.appendLine('codoc: starting the daemon…');
        startDaemon(execs.codoc, rootDir);
        await vscode.commands.executeCommand('setContext', 'codoc.ready', true);
        void vscode.window.showInformationMessage('codoc is set up.', 'Open Tree').then(c => { if (c === 'Open Tree') void vscode.commands.executeCommand('codoc.open'); });
    } catch (err) {
        if (err instanceof ProvisionCancelledError) return;
        const msg = err instanceof WorkspaceUntrustedError ? err.message : `codoc setup failed: ${(err as Error).message}`;
        log.appendLine(`codoc: ${msg}`);
        const choice = await vscode.window.showErrorMessage(msg, 'Retry', 'View Log');
        if (choice === 'Retry') await runSetup(context, state);
        else if (choice === 'View Log') log.show();
    } finally {
        state.setProvisioning(false);
    }
}

export function activate(context: vscode.ExtensionContext): void {
    const state = new Workspace();
    context.subscriptions.push(state);

    /** Run a codoc command in a terminal, where the person can watch and interrupt it. */
    const term = (name: string, args: string[], cwd = state.rootDir) => {
        const codoc = cachedExecutables(context)?.codoc;
        if (!codoc || !cwd) { void vscode.window.showWarningMessage('codoc is not set up yet. Run "codoc: Set up codoc".'); return; }
        const t = vscode.window.createTerminal({ name: `codoc ${name}`, cwd });
        t.show(true);
        t.sendText(commandLine([codoc, ...args]));
    };

    context.subscriptions.push(vscode.window.registerCustomEditorProvider(
        CodocEditorProvider.viewType, new CodocEditorProvider(context, root => term('realize', ['realize'], root)),
        { webviewOptions: { retainContextWhenHidden: true }, supportsMultipleEditorsPerDocument: false }));

    const reconcileDaemon = (): void => {
        if (!vscode.workspace.isTrusted || !state.rootDir || !state.initialized) return;
        const execs = cachedExecutables(context);
        if (execs) startDaemon(execs.codoc, state.rootDir);
    };
    reconcileDaemon();
    const daemonTicker = setInterval(reconcileDaemon, DAEMON_RECONCILE_MS);
    context.subscriptions.push({ dispose: () => clearInterval(daemonTicker) },
        vscode.workspace.onDidGrantWorkspaceTrust(() => reconcileDaemon()),
        state.onDidChange(() => reconcileDaemon()),
        { dispose: () => stopDaemon() });

    const bar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
    const paint = () => {
        const v = statusBarView(state, !!cachedExecutables(context));
        bar.text = v.text; bar.tooltip = v.tooltip; bar.command = v.command;
        bar.backgroundColor = v.warn ? new vscode.ThemeColor('statusBarItem.warningBackground') : undefined;
        bar.show();
    };
    paint();
    context.subscriptions.push(bar, state.onDidChange(paint));

    const staticDir = vscode.Uri.joinPath(context.extensionUri, 'dist', 'webview').fsPath;
    const serve = (tunnel: boolean) => { if (state.rootDir) term('serve', serveArgs({ root: state.rootDir, port: DEFAULT_HUB_PORT, staticDir, tunnel })); };
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.setup', () => runSetup(context, state)),
        vscode.commands.registerCommand('codoc.startDaemon', () => {
            if (!state.rootDir) return;
            // A person asked, so try again whatever happened last time.
            resetDaemonBackoff();
            const execs = cachedExecutables(context);
            if (!execs) { void vscode.commands.executeCommand('codoc.setup'); return; }
            if (!startDaemon(execs.codoc, state.rootDir)) void vscode.window.showInformationMessage('A codoc daemon is already running for this repo.');
        }),
        vscode.commands.registerCommand('codoc.open', async () => {
            if (!state.codocDir) { void vscode.window.showInformationMessage('Open a folder with a .codoc workspace, or run "codoc: Set up codoc".'); return; }
            // The editor is registered for this file. Until the daemon has
            // published its export, an empty one stands in as the handle.
            const uri = vscode.Uri.file(path.join(state.codocDir, 'tree.codoc'));
            if (!fs.existsSync(uri.fsPath)) fs.writeFileSync(uri.fsPath, '', 'utf8');
            await vscode.commands.executeCommand('vscode.openWith', uri, CodocEditorProvider.viewType);
        }),
        vscode.commands.registerCommand('codoc.sync', () => term('sync', ['sync'])),
        vscode.commands.registerCommand('codoc.realize', () => term('realize', ['realize'])),
        vscode.commands.registerCommand('codoc.serve.start', () => serve(false)),
        vscode.commands.registerCommand('codoc.serve.startTunnel', () => serve(true)),
        vscode.commands.registerCommand('codoc.find', () => CodocEditorProvider.find(false)),
        vscode.commands.registerCommand('codoc.replace', () => CodocEditorProvider.find(true)),
        vscode.window.registerUriHandler({ handleUri(uri) { if (uri.path === '/setup') void vscode.commands.executeCommand('codoc.setup'); } }),
    );

    // A folder with neither a tree nor an installed core: offer setup once per activation.
    if (state.rootDir && !state.initialized && !cachedExecutables(context) && vscode.workspace.isTrusted) {
        void vscode.window.showInformationMessage('codoc can index this repo and build a feature tree for it.', 'Set up codoc')
            .then(c => { if (c === 'Set up codoc') void vscode.commands.executeCommand('codoc.setup'); });
    }
}

export function deactivate(): void {
    stopDaemon();
}
