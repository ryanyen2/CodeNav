/**
 * extension.ts — codoc for VS Code, v2.
 *
 * The extension provisions the Python core, keeps the daemon running as a
 * reconciled invariant, registers the Codoc Tree editor, and offers a handful
 * of commands. It holds no model of the tree: the editor reads the daemon's
 * projection and writes ops to the daemon's inbox (host/provider.ts).
 */
import * as cp from 'node:child_process';
import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';

import { reapStaleLock, resetDaemonBackoff, startDaemon, stopDaemon } from './daemon/daemon-manager';
import { CodocEditorProvider } from './host/provider';
import { Workspace, statusBarView } from './host/workspace';
import { DEFAULT_HUB_PORT, hubUrl, serveArgs } from './serve/serve-manager';
import { bootstrapCredentials } from './setup/credentials';
import {
    ProvisionCancelledError, WorkspaceUntrustedError, cachedExecutables, ensureUv, provisionCodoc,
} from './setup/provision';
import { SETUP_STEPS, needsSetup } from './setup/setup-flow';

let channel: vscode.OutputChannel | undefined;
const out = (): vscode.OutputChannel => (channel ??= vscode.window.createOutputChannel('codoc'));

const DAEMON_RECONCILE_MS = 15_000;

function runCodoc(codocPath: string, args: readonly string[], rootDir: string): Promise<void> {
    return new Promise((resolve, reject) => {
        const child = cp.spawn(codocPath, args, { cwd: rootDir, env: { ...process.env, PYTHONUNBUFFERED: '1' } });
        child.stdout.on('data', d => out().append(String(d)));
        child.stderr.on('data', d => out().append(String(d)));
        child.on('error', reject);
        child.on('close', code => (code === 0 ? resolve() : reject(new Error(`codoc ${args[0]} exited with ${code}`))));
    });
}

async function runSetup(context: vscode.ExtensionContext, state: Workspace): Promise<void> {
    if (!vscode.workspace.isTrusted) {
        const choice = await vscode.window.showWarningMessage('codoc installs and runs a Python core, so it needs a trusted workspace.', 'Trust Workspace');
        if (choice === 'Trust Workspace') await vscode.commands.executeCommand('workbench.trust.manage');
        return;
    }
    const rootDir = state.rootDir;
    if (!rootDir) { void vscode.window.showInformationMessage('Open a folder before setting up codoc.'); return; }
    state.setProvisioning(true);
    try {
        out().appendLine(`codoc: ${SETUP_STEPS[0].label}`);
        const uv = await ensureUv();
        out().appendLine(`codoc: ${SETUP_STEPS[1].label}`);
        const execs = await provisionCodoc(context, uv);
        out().appendLine(`codoc: ${SETUP_STEPS[2].label}`);
        await bootstrapCredentials(context, rootDir);
        out().appendLine(`codoc: ${SETUP_STEPS[3].label}`);
        if (!fs.existsSync(path.join(rootDir, '.codoc', 'log'))) {
            const legacy = path.join(rootDir, '.codoc', 'codoc.db');
            const args = fs.existsSync(legacy) ? ['import-v1', '--root', rootDir] : ['init', '--root', rootDir];
            await vscode.window.withProgress(
                { location: vscode.ProgressLocation.Notification, title: 'codoc: indexing your repo and building the feature tree…' },
                () => runCodoc(execs.codoc, args, rootDir),
            );
        }
        out().appendLine(`codoc: ${SETUP_STEPS[4].label}`);
        reapStaleLock(rootDir);
        startDaemon(context, execs.codoc, rootDir);
        await vscode.commands.executeCommand('setContext', 'codoc.ready', true);
        void vscode.window.showInformationMessage('codoc is set up.', 'Open Tree').then(c => { if (c === 'Open Tree') void vscode.commands.executeCommand('codoc.open'); });
    } catch (err) {
        if (err instanceof ProvisionCancelledError) return;
        const msg = err instanceof WorkspaceUntrustedError ? err.message : `codoc setup failed: ${(err as Error).message}`;
        out().appendLine(`codoc: ${msg}`);
        const choice = await vscode.window.showErrorMessage(msg, 'Retry', 'View Log');
        if (choice === 'Retry') await runSetup(context, state);
        else if (choice === 'View Log') out().show();
    } finally {
        state.setProvisioning(false);
    }
}

export function activate(context: vscode.ExtensionContext): void {
    const state = new Workspace();
    context.subscriptions.push(state);
    const cli = () => cachedExecutables(context)?.codoc;

    context.subscriptions.push(vscode.window.registerCustomEditorProvider(
        CodocEditorProvider.viewType, new CodocEditorProvider(context, cli),
        { webviewOptions: { retainContextWhenHidden: true }, supportsMultipleEditorsPerDocument: false }));

    // The daemon as a reconciled invariant: trusted, initialized, provisioned,
    // nobody else writing, no live daemon → start one. Idempotent; backs off.
    const reconcileDaemon = (): void => {
        if (!vscode.workspace.isTrusted || !state.rootDir || !state.initialized) return;
        // Another writer (the study's replay harness) owns the workspace: stand down.
        if (fs.existsSync(path.join(state.rootDir, '.codoc', 'replay.lock'))) return;
        const execs = cachedExecutables(context);
        if (!execs) return;
        reapStaleLock(state.rootDir);
        startDaemon(context, execs.codoc, state.rootDir);
    };
    reconcileDaemon();
    const daemonTicker = setInterval(reconcileDaemon, DAEMON_RECONCILE_MS);
    context.subscriptions.push({ dispose: () => clearInterval(daemonTicker) },
        vscode.workspace.onDidGrantWorkspaceTrust(() => reconcileDaemon()),
        state.onDidChange(() => reconcileDaemon()),
        { dispose: () => stopDaemon() });
    if (state.codocDir) {
        const lock = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(vscode.Uri.file(state.codocDir), 'replay.lock'));
        context.subscriptions.push(lock, lock.onDidCreate(() => stopDaemon()), lock.onDidDelete(() => reconcileDaemon()));
    }

    const bar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
    const paint = () => {
        const v = statusBarView(state, !!cachedExecutables(context));
        bar.text = v.text; bar.tooltip = v.tooltip; bar.command = v.command;
        bar.backgroundColor = v.warn ? new vscode.ThemeColor('statusBarItem.warningBackground') : undefined;
        bar.show();
    };
    paint();
    context.subscriptions.push(bar, state.onDidChange(paint));

    const term = (name: string, args: string[]) => {
        const c = cli();
        if (!c || !state.rootDir) { void vscode.window.showWarningMessage('codoc is not set up yet.'); return; }
        const t = vscode.window.createTerminal({ name: `codoc ${name}`, cwd: state.rootDir });
        t.show(true);
        t.sendText([c, ...args].map(a => (/\s/.test(a) ? `"${a}"` : a)).join(' '));
    };
    const staticDir = vscode.Uri.joinPath(context.extensionUri, 'dist', 'webview').fsPath;
    context.subscriptions.push(
        vscode.commands.registerCommand('codoc.setup', () => runSetup(context, state)),
        vscode.commands.registerCommand('codoc.startDaemon', () => {
            if (!state.rootDir) return;
            resetDaemonBackoff();
            const execs = cachedExecutables(context);
            if (!execs) { void vscode.commands.executeCommand('codoc.setup'); return; }
            reapStaleLock(state.rootDir);
            if (!startDaemon(context, execs.codoc, state.rootDir)) void vscode.window.showInformationMessage('A codoc daemon is already running for this repo.');
        }),
        vscode.commands.registerCommand('codoc.open', async () => {
            if (!state.codocDir) { void vscode.window.showInformationMessage('Open a folder with a .codoc workspace, or run "codoc: Set up codoc".'); return; }
            const uri = vscode.Uri.file(path.join(state.codocDir, 'tree.codoc'));
            if (!fs.existsSync(uri.fsPath)) fs.writeFileSync(uri.fsPath, '', 'utf8');
            await vscode.commands.executeCommand('vscode.openWith', uri, CodocEditorProvider.viewType);
        }),
        vscode.commands.registerCommand('codoc.sync', () => term('sync', ['sync'])),
        vscode.commands.registerCommand('codoc.realize', () => term('realize', ['realize'])),
        vscode.commands.registerCommand('codoc.serve.start', () => state.rootDir && term('serve', serveArgs({ root: state.rootDir, port: DEFAULT_HUB_PORT, staticDir }))),
        vscode.commands.registerCommand('codoc.serve.startTunnel', () => state.rootDir && term('serve', serveArgs({ root: state.rootDir, port: DEFAULT_HUB_PORT, staticDir, tunnel: true }))),
        vscode.commands.registerCommand('codoc.serve.stop', () => { void vscode.window.showInformationMessage(`Stop the terminal running the hub (${hubUrl()}).`); }),
        vscode.commands.registerCommand('codoc.navigateToFeature', (fid: string) => CodocEditorProvider.reveal(fid)),
        vscode.commands.registerCommand('codoc.find', () => CodocEditorProvider.find(false)),
        vscode.commands.registerCommand('codoc.replace', () => CodocEditorProvider.find(true)),
        vscode.window.registerUriHandler({ handleUri(uri) { if (uri.path === '/setup') void vscode.commands.executeCommand('codoc.setup'); } }),
    );

    if (state.rootDir && needsSetup(state.initialized, !!cachedExecutables(context)) && vscode.workspace.isTrusted) {
        void vscode.window.showInformationMessage('codoc can index this repo and build a feature tree for it.', 'Set up codoc')
            .then(c => { if (c === 'Set up codoc') void vscode.commands.executeCommand('codoc.setup'); });
    }
}

export function deactivate(): void {
    stopDaemon();
}
