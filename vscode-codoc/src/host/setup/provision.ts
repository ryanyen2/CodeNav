/**
 * provision.ts — installs the codoc Python core and remembers where it landed.
 *
 * `ensureUv` finds `uv` on PATH or runs its standalone installer.
 * `provisionCodoc` installs Python 3.11 and the wheel shipped in `bundled/`
 * into an isolated `uv tool` environment, then asks uv where the console
 * scripts are and caches those paths in global state. `cachedExecutables`
 * reads the cache, and answers undefined when a cached file is gone.
 *
 * The `codoc.serverPath` setting replaces the installed `codoc` with another
 * binary, for developing the Python side against this extension.
 *
 * Both entry points refuse an untrusted workspace. Child processes are spawned
 * from argument vectors; the one shell command is uv's installer, a constant.
 * The pure parts are in setup/paths.ts.
 */
import * as vscode from 'vscode';
import * as cp from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { output } from '../output';
import {
    CodocExecutables, CODOC_PYTHON_VERSION,
    executablePaths, findBundledWheel, shouldReresolve, uvInstalledPath,
    uvInstallerCommand, uvPythonInstallArgs, uvToolDirBinArgs, uvToolInstallArgs,
    uvVersionArgs,
} from './paths';

/** Global-state keys for the cached executable paths. */
const KEY_CODOC = 'codoc.exec.codoc';
const KEY_CODOC_MCP = 'codoc.exec.codocMcp';

export class WorkspaceUntrustedError extends Error {
    constructor() {
        super('codoc setup requires a trusted workspace. Trust this workspace and try again.');
        this.name = 'WorkspaceUntrustedError';
    }
}

/** The person cancelled a provisioning step. */
export class ProvisionCancelledError extends Error {
    constructor() {
        super('codoc setup was cancelled.');
        this.name = 'ProvisionCancelledError';
    }
}

interface RunResult {
    code: number | null;
    stdout: string;
}

/** Run a command, copying its output to the channel. `UV_NO_MODIFY_PATH` keeps
 *  uv from editing the person's shell profile. Cancelling kills the child and
 *  rejects with `ProvisionCancelledError`. */
function run(command: string, args: readonly string[], opts: { shell?: boolean; token?: vscode.CancellationToken } = {}): Promise<RunResult> {
    const log = output();
    log.appendLine(`$ ${command} ${args.join(' ')}`);
    return new Promise<RunResult>((resolve, reject) => {
        const child = cp.spawn(command, args as string[], {
            env: { ...process.env, UV_NO_MODIFY_PATH: '1' },
            shell: opts.shell ?? false,
        });
        let stdout = '';
        let cancelled = false;
        const cancelSub = opts.token?.onCancellationRequested(() => {
            cancelled = true;
            log.appendLine('cancelled; stopping the child process');
            child.kill();
        });
        child.stdout?.on('data', (buf: Buffer) => {
            const s = buf.toString();
            stdout += s;
            log.append(s);
        });
        child.stderr?.on('data', (buf: Buffer) => log.append(buf.toString()));
        child.on('error', err => {
            cancelSub?.dispose();
            reject(err);
        });
        child.on('close', code => {
            cancelSub?.dispose();
            if (cancelled) reject(new ProvisionCancelledError());
            else resolve({ code, stdout });
        });
    });
}

function assertTrusted(): void {
    if (!vscode.workspace.isTrusted) throw new WorkspaceUntrustedError();
}

/** A usable `uv`: the one on PATH, or the one the standalone installer puts
 *  in `~/.local/bin`. */
export async function ensureUv(token?: vscode.CancellationToken): Promise<string> {
    assertTrusted();
    const log = output();
    try {
        const probe = await run('uv', uvVersionArgs(), { token });
        if (probe.code === 0) {
            log.appendLine(`uv present: ${probe.stdout.trim()}`);
            return 'uv';
        }
    } catch {
        // not on PATH
    }

    log.appendLine('uv not found; running the standalone installer');
    const installer = uvInstallerCommand(process.platform);
    const res = await run(installer.command, installer.args, { shell: installer.shell, token });
    if (res.code !== 0) {
        throw new Error(`uv standalone installer failed (exit ${res.code}). See the codoc output channel.`);
    }
    const installed = uvInstalledPath(os.homedir(), process.platform);
    if (!fs.existsSync(installed)) {
        throw new Error(`uv was installed but not found at ${installed}. See the codoc output channel.`);
    }
    log.appendLine(`uv installed at ${installed}`);
    return installed;
}

function bundledWheelPath(context: vscode.ExtensionContext): string {
    const dir = vscode.Uri.joinPath(context.extensionUri, 'bundled').fsPath;
    let listing: string[];
    try {
        listing = fs.readdirSync(dir);
    } catch (e) {
        throw new Error(`Could not read the bundled wheel directory (${dir}): ${(e as Error).message}`);
    }
    return path.join(dir, findBundledWheel(listing));
}

/** Install Python and the bundled wheel under a cancellable progress
 *  notification, and return the paths of the installed scripts. */
export async function provisionCodoc(context: vscode.ExtensionContext, uvPath: string): Promise<CodocExecutables> {
    assertTrusted();
    const wheel = bundledWheelPath(context);
    return vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'Setting up codoc', cancellable: true },
        async (progress, token): Promise<CodocExecutables> => {
            progress.report({ message: `installing Python ${CODOC_PYTHON_VERSION}…` });
            const py = await run(uvPath, uvPythonInstallArgs(), { token });
            if (py.code !== 0) {
                throw new Error(`uv python install failed (exit ${py.code}). See the codoc output channel.`);
            }
            progress.report({ message: 'installing the codoc core (this can take a few minutes)…' });
            const tool = await run(uvPath, uvToolInstallArgs(wheel), { token });
            if (tool.code !== 0) {
                throw new Error(`uv tool install failed (exit ${tool.code}). See the codoc output channel.`);
            }
            progress.report({ message: 'resolving executable paths…' });
            return resolvePaths(context, uvPath, token);
        },
    );
}

/** Ask uv where the console scripts are and cache the answer. */
async function resolvePaths(context: vscode.ExtensionContext, uvPath: string, token: vscode.CancellationToken): Promise<CodocExecutables> {
    const res = await run(uvPath, uvToolDirBinArgs(), { token });
    if (res.code !== 0) {
        throw new Error(`uv tool dir --bin failed (exit ${res.code}). See the codoc output channel.`);
    }
    const execs = executablePaths(res.stdout, process.platform);
    await context.globalState.update(KEY_CODOC, execs.codoc);
    await context.globalState.update(KEY_CODOC_MCP, execs.codocMcp);
    output().appendLine(`resolved codoc=${execs.codoc}  codoc-mcp=${execs.codocMcp}`);
    return withServerPath(execs);
}

/** The cached executable paths, or undefined when either is uncached or no
 *  longer on disk, which means setup has to run. */
export function cachedExecutables(context: vscode.ExtensionContext): CodocExecutables | undefined {
    const codoc = context.globalState.get<string>(KEY_CODOC);
    const codocMcp = context.globalState.get<string>(KEY_CODOC_MCP);
    if (shouldReresolve(codoc, !!codoc && fs.existsSync(codoc))) return undefined;
    if (shouldReresolve(codocMcp, !!codocMcp && fs.existsSync(codocMcp))) return undefined;
    return withServerPath({ codoc: codoc!, codocMcp: codocMcp! });
}

/** Apply the `codoc.serverPath` setting: when set, it is the `codoc` to run. */
function withServerPath(execs: CodocExecutables): CodocExecutables {
    const override = vscode.workspace.getConfiguration('codoc').get<string>('serverPath')?.trim();
    return override ? { ...execs, codoc: override } : execs;
}
