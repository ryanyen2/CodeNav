/**
 * provision.ts — vscode-facing provisioning orchestration (U2).
 *
 * Installs the codoc Python core into an isolated, version-pinned `uv` tool env
 * with zero manual steps, and caches the resolved `codoc` / `codoc-mcp` paths:
 *
 *   ensureUv      — probe for `uv`; bootstrap via the standalone installer if absent.
 *   provisionCodoc — `uv python install 3.11` → `uv tool install <bundled wheel>`,
 *                    wrapped in a cancellable progress notification, streamed to a
 *                    "codoc" OutputChannel.
 *   resolvePaths  — `uv tool dir --bin` → cache the executable paths in globalState,
 *                   re-validating (existsSync) on every call.
 *
 * All pure logic (argv builders, output parsing, platform branches, the
 * re-resolve decision) lives in `./paths` so it stays vitest-testable. This file
 * is the vscode wiring and is intentionally NOT covered by vitest.
 *
 * Security: spawning/installing is gated behind Workspace Trust (defended here
 * AND by the U4 caller). Child processes get an explicit `env`; no untrusted
 * input is ever interpolated into a shell string.
 */

import * as vscode from 'vscode';
import * as cp from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import {
    CommandSpec, CodocExecutables, CODOC_PYTHON_VERSION,
    executablePaths, findBundledWheel, shouldReresolve, uvInstalledPath,
    uvInstallerCommand, uvPythonInstallArgs, uvToolDirBinArgs, uvToolInstallArgs,
    uvVersionArgs,
} from './paths';

/** globalState keys for the resolved, re-validatable executable paths. */
const KEY_CODOC = 'codoc.exec.codoc';
const KEY_CODOC_MCP = 'codoc.exec.codocMcp';
const KEY_UV = 'codoc.exec.uv';

/** Raised when provisioning is attempted in an untrusted workspace (R5/KTD6). */
export class WorkspaceUntrustedError extends Error {
    constructor() {
        super('codoc setup requires a trusted workspace. Trust this workspace and try again.');
        this.name = 'WorkspaceUntrustedError';
    }
}

/** Raised when the user cancels an in-flight provisioning step. */
export class ProvisionCancelledError extends Error {
    constructor() {
        super('codoc setup was cancelled.');
        this.name = 'ProvisionCancelledError';
    }
}

/** Result of a completed child process. */
interface RunResult {
    code: number | null;
    stdout: string;
    stderr: string;
}

/** Get (or lazily create) the shared "codoc" OutputChannel. */
let _channel: vscode.OutputChannel | undefined;
function outputChannel(): vscode.OutputChannel {
    if (!_channel) _channel = vscode.window.createOutputChannel('codoc');
    return _channel;
}

/** The child env: inherit the host's, with `UV_NO_MODIFY_PATH=1` so installs/tools
 *  never edit the user's shell profiles. Callers may layer more on top. */
function childEnv(extra?: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
    return { ...process.env, UV_NO_MODIFY_PATH: '1', ...extra };
}

/**
 * Spawn `command args…`, streaming stdout/stderr to the OutputChannel, resolving
 * with the captured streams + exit code. Honours a cancellation token by killing
 * the child (rejecting with {@link ProvisionCancelledError}).
 *
 * `spec.shell` only ever true for the constant uv-installer pipeline (no untrusted
 * input in argv); every other call is argv-only with `shell: false`.
 */
function run(
    command: string,
    args: readonly string[],
    opts: { cwd?: string; env?: NodeJS.ProcessEnv; shell?: boolean; token?: vscode.CancellationToken } = {},
): Promise<RunResult> {
    const channel = outputChannel();
    channel.appendLine(`$ ${command} ${args.join(' ')}`);
    return new Promise<RunResult>((resolve, reject) => {
        const child = cp.spawn(command, args as string[], {
            cwd: opts.cwd,
            env: opts.env ?? childEnv(),
            shell: opts.shell ?? false,
        });

        let stdout = '';
        let stderr = '';
        let cancelled = false;

        const cancelSub = opts.token?.onCancellationRequested(() => {
            cancelled = true;
            channel.appendLine('… cancelled — terminating child process');
            child.kill();
        });

        child.stdout?.on('data', (buf: Buffer) => {
            const s = buf.toString();
            stdout += s;
            channel.append(s);
        });
        child.stderr?.on('data', (buf: Buffer) => {
            const s = buf.toString();
            stderr += s;
            channel.append(s);
        });
        child.on('error', err => {
            cancelSub?.dispose();
            reject(err);
        });
        child.on('close', code => {
            cancelSub?.dispose();
            if (cancelled) {
                reject(new ProvisionCancelledError());
                return;
            }
            resolve({ code, stdout, stderr });
        });
    });
}

/** Throw if the workspace is untrusted (defence-in-depth alongside the U4 caller). */
function assertTrusted(): void {
    if (!vscode.workspace.isTrusted) throw new WorkspaceUntrustedError();
}

/**
 * Probe for `uv`; if absent, run the standalone installer (with
 * `UV_NO_MODIFY_PATH=1`) and return the known absolute install path. The host's
 * PATH is NOT relied upon post-install — we use `uvInstalledPath(...)` directly.
 *
 * @returns the absolute path to a usable `uv` executable.
 */
export async function ensureUv(token?: vscode.CancellationToken): Promise<string> {
    assertTrusted();
    const channel = outputChannel();

    // 1) Already on PATH? `uv --version` is the cheap presence probe.
    try {
        const probe = await run('uv', uvVersionArgs(), { token });
        if (probe.code === 0) {
            channel.appendLine(`uv present: ${probe.stdout.trim()}`);
            return 'uv';
        }
    } catch {
        // Not on PATH (ENOENT) — fall through to the installer.
    }

    // 2) Bootstrap via the standalone installer for this platform.
    channel.appendLine('uv not found — running the standalone installer…');
    const installer: CommandSpec = uvInstallerCommand(process.platform);
    const res = await run(installer.command, installer.args, { shell: installer.shell, token });
    if (res.code !== 0) {
        throw new Error(`uv standalone installer failed (exit ${res.code}). See the codoc output channel.`);
    }

    // 3) Use the known install path directly — never trust a refreshed PATH.
    const installed = uvInstalledPath(os.homedir(), process.platform);
    if (!fs.existsSync(installed)) {
        throw new Error(`uv was installed but not found at ${installed}. See the codoc output channel.`);
    }
    channel.appendLine(`uv installed at ${installed}`);
    return installed;
}

/** Locate the single bundled wheel under `<extensionUri>/bundled/`. */
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

/**
 * Install codoc into an isolated uv tool env:
 *   `uv python install 3.11` → `uv tool install --python 3.11 <bundled wheel>`.
 * Cancellable; output streamed to the "codoc" channel; the child is killed on
 * cancellation. Resolves the executable paths and caches them.
 *
 * @returns the resolved `codoc` / `codoc-mcp` paths.
 */
export async function provisionCodoc(
    context: vscode.ExtensionContext,
    uvPath: string,
): Promise<CodocExecutables> {
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

/**
 * Resolve the codoc executable paths via `uv tool dir --bin`, persist them in
 * globalState, and re-validate the cached values with `fs.existsSync` — re-running
 * the resolution when the cache is missing or stale.
 *
 * @returns the resolved `codoc` / `codoc-mcp` paths.
 */
export async function resolvePaths(
    context: vscode.ExtensionContext,
    uvPath: string,
    token?: vscode.CancellationToken,
): Promise<CodocExecutables> {
    const cachedCodoc = context.globalState.get<string>(KEY_CODOC);
    const cachedMcp = context.globalState.get<string>(KEY_CODOC_MCP);

    const stale = shouldReresolve(cachedCodoc, !!cachedCodoc && fs.existsSync(cachedCodoc))
        || shouldReresolve(cachedMcp, !!cachedMcp && fs.existsSync(cachedMcp));

    if (!stale && cachedCodoc && cachedMcp) {
        return { codoc: cachedCodoc, codocMcp: cachedMcp };
    }

    const res = await run(uvPath, uvToolDirBinArgs(), { token });
    if (res.code !== 0) {
        throw new Error(`uv tool dir --bin failed (exit ${res.code}). See the codoc output channel.`);
    }
    const execs = executablePaths(res.stdout, process.platform);

    await context.globalState.update(KEY_CODOC, execs.codoc);
    await context.globalState.update(KEY_CODOC_MCP, execs.codocMcp);
    await context.globalState.update(KEY_UV, uvPath);
    outputChannel().appendLine(`resolved codoc=${execs.codoc}  codoc-mcp=${execs.codocMcp}`);
    return applyServerPathOverride(execs);
}

/**
 * Read the cached executable paths without re-resolving — for fast activation
 * checks. Returns `undefined` when either path is uncached or no longer on disk
 * (the caller should re-provision / re-resolve).
 */
/** The `codoc.serverPath` override (trimmed) or undefined. When set, it wins over the
 *  auto-provisioned uv-tool path for the daemon + `codoc init` — letting a developer
 *  point the extension at updated server code (a dev checkout's venv binary) without
 *  reinstalling the tool. */
function serverPathOverride(): string | undefined {
    const p = vscode.workspace.getConfiguration('codoc').get<string>('serverPath');
    return p && p.trim() ? p.trim() : undefined;
}

/** Apply the `codoc.serverPath` override to a resolved set of executables (the CLI path
 *  only — the file-based MCP config is written separately). No-op when unset. */
export function applyServerPathOverride(execs: CodocExecutables): CodocExecutables {
    const override = serverPathOverride();
    return override ? { ...execs, codoc: override } : execs;
}

export function cachedExecutables(context: vscode.ExtensionContext): CodocExecutables | undefined {
    const codoc = context.globalState.get<string>(KEY_CODOC);
    const codocMcp = context.globalState.get<string>(KEY_CODOC_MCP);
    if (shouldReresolve(codoc, !!codoc && fs.existsSync(codoc))) return undefined;
    if (shouldReresolve(codocMcp, !!codocMcp && fs.existsSync(codocMcp))) return undefined;
    return applyServerPathOverride({ codoc: codoc!, codocMcp: codocMcp! });
}

/** The cached uv path (or `undefined` if not yet provisioned). */
export function cachedUvPath(context: vscode.ExtensionContext): string | undefined {
    return context.globalState.get<string>(KEY_UV);
}
