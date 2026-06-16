/**
 * paths.ts — PURE, vscode-free provisioning helpers (vitest-testable).
 *
 * Every piece of provisioning logic that can be expressed without `vscode` or
 * spawning a process lives here so it can be exercised by `src/test/**`:
 *   • parsing `uv tool dir --bin` output → the `codoc` / `codoc-mcp` executable
 *     paths (platform-aware: bare names on Unix, `.exe` on Windows);
 *   • choosing the uv standalone-installer command for a `NodeJS.Platform`;
 *   • building the `uv tool install` / `uv python install` argv arrays;
 *   • locating the bundled wheel inside a directory listing;
 *   • deciding whether a cached resolved path must be re-resolved.
 *
 * The thin `vscode` orchestration (withProgress / OutputChannel / globalState /
 * actually running child processes) lives in `provision.ts`, which imports these.
 *
 * No `import 'vscode'` here, ever — `vitest.config.ts` runs `src/test/**` against
 * these and the modules under test must not pull in the vscode host shim.
 */

import * as path from 'node:path';

/** The Python version codoc is pinned to (KTD1: isolated, version-pinned env). */
export const CODOC_PYTHON_VERSION = '3.11';

/** The two console scripts `uv tool install` exposes for the codoc package. */
const SCRIPT_NAMES = ['codoc', 'codoc-mcp'] as const;

/** Resolved absolute paths to codoc's installed console scripts. */
export interface CodocExecutables {
    /** Absolute path to the `codoc` CLI. */
    readonly codoc: string;
    /** Absolute path to the `codoc-mcp` FastMCP server entry point. */
    readonly codocMcp: string;
}

/** A command spec to run (never a shell string — argv-only, no interpolation). */
export interface CommandSpec {
    /** The executable to run. */
    readonly command: string;
    /** Argument vector (no shell parsing). */
    readonly args: readonly string[];
    /**
     * True when the args must be handed to a shell to evaluate a pipeline
     * (the uv standalone installer is a documented `curl … | sh` / `irm … | iex`
     * one-liner). Caller passes `{ shell: true }` to `child_process` for these.
     */
    readonly shell: boolean;
}

/** Windows uses `.exe` copies; Unix uses extension-less symlinks. */
function exeSuffix(platform: NodeJS.Platform): string {
    return platform === 'win32' ? '.exe' : '';
}

/**
 * Map the `uv tool dir --bin` output (the directory holding the installed
 * console scripts) to the absolute `codoc` / `codoc-mcp` paths for `platform`.
 *
 * @param binDir trimmed `uv tool dir --bin` stdout (a single directory path).
 * @param platform a `process.platform` value (`'win32'` → `.exe`, else bare).
 */
export function executablePaths(binDir: string, platform: NodeJS.Platform): CodocExecutables {
    const dir = binDir.trim();
    if (dir.length === 0) {
        throw new Error('uv tool dir --bin returned an empty path — cannot resolve codoc executables.');
    }
    const suffix = exeSuffix(platform);
    return {
        codoc: path.join(dir, `${SCRIPT_NAMES[0]}${suffix}`),
        codocMcp: path.join(dir, `${SCRIPT_NAMES[1]}${suffix}`),
    };
}

/**
 * The standalone-installer command for bootstrapping `uv` when it isn't already
 * present. On darwin/linux this is the documented `curl … | sh`; on win32 it is
 * the PowerShell `irm … | iex` form. Caller runs it with `UV_NO_MODIFY_PATH=1`
 * (so it never edits shell profiles) and then uses the known install path.
 *
 * The returned spec is `shell: true` because both forms are pipelines the OS
 * shell must evaluate — but the argv carries no untrusted input (the URLs are
 * constants), so there is no injection surface.
 */
export function uvInstallerCommand(platform: NodeJS.Platform): CommandSpec {
    if (platform === 'win32') {
        // PowerShell: download the installer script and pipe it to the interpreter.
        return {
            command: 'powershell',
            args: ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', 'irm https://astral.sh/uv/install.ps1 | iex'],
            shell: true,
        };
    }
    // darwin / linux (and any other POSIX): curl the installer, pipe to sh.
    return {
        command: 'sh',
        args: ['-c', 'curl -LsSf https://astral.sh/uv/install.sh | sh'],
        shell: true,
    };
}

/**
 * The absolute path the standalone installer drops the `uv` binary at, given a
 * user home directory. The extension uses this directly after install rather
 * than relying on the extension host's PATH being refreshed.
 *
 * @param home `os.homedir()` (or `%USERPROFILE%` on Windows).
 */
export function uvInstalledPath(home: string, platform: NodeJS.Platform): string {
    return platform === 'win32'
        ? path.join(home, '.local', 'bin', `uv${exeSuffix(platform)}`)
        : path.join(home, '.local', 'bin', 'uv');
}

/** Build the `uv python install <version>` argv (pin CPython for the tool env). */
export function uvPythonInstallArgs(version: string = CODOC_PYTHON_VERSION): string[] {
    return ['python', 'install', version];
}

/**
 * Build the `uv tool install --python <version> --with claude-agent-sdk <wheel>`
 * argv. Installs all of codoc's console scripts into the isolated tool env against
 * the pinned Python. `--with claude-agent-sdk` layers the SDK into the same env
 * (the plan's `codoc[sdk]` intent) so `codoc realize`'s SDK engine is available —
 * a bare wheel path can't carry an extra, so `--with` is the uv-supported form.
 *
 * `--reinstall` is required: the bundled wheel can change without a version bump
 * (it's a path install, rebuilt by `bundle-wheel`), so a same-version `uv tool
 * install` would otherwise no-op and leave a stale build in place — exactly the
 * trap that shipped a wheel missing its prompt files.
 *
 * @param wheelPath absolute path to the bundled `codoc-*.whl`.
 * @param version the pinned Python version (defaults to {@link CODOC_PYTHON_VERSION}).
 */
export function uvToolInstallArgs(wheelPath: string, version: string = CODOC_PYTHON_VERSION): string[] {
    if (wheelPath.trim().length === 0) {
        throw new Error('uvToolInstallArgs: a wheel path is required.');
    }
    return ['tool', 'install', '--reinstall', '--python', version, '--with', 'claude-agent-sdk', wheelPath];
}

/** Build the `uv tool dir --bin` argv (the bin-dir discovery query). */
export function uvToolDirBinArgs(): string[] {
    return ['tool', 'dir', '--bin'];
}

/** Build the `uv --version` argv (the cheap presence probe). */
export function uvVersionArgs(): string[] {
    return ['--version'];
}

/**
 * Locate the single bundled codoc wheel within a directory listing. The VSIX
 * ships exactly one `codoc-<ver>-py3-none-any.whl`; zero or several is a
 * packaging error we surface clearly rather than guessing.
 *
 * @param listing the file names (not full paths) found in `bundled/`.
 * @returns the matching wheel file name.
 */
export function findBundledWheel(listing: readonly string[]): string {
    const wheels = listing.filter(name => name.startsWith('codoc') && name.endsWith('.whl'));
    if (wheels.length === 0) {
        throw new Error('No bundled codoc wheel found (expected exactly one codoc-*.whl). Re-run the wheel bundling step.');
    }
    if (wheels.length > 1) {
        throw new Error(`Multiple bundled codoc wheels found (${wheels.join(', ')}) — expected exactly one.`);
    }
    return wheels[0];
}

/**
 * Decide whether a cached resolved executable path must be re-resolved.
 * Re-resolve when there is no cached value or the cached file no longer exists
 * on disk (a stale cache — e.g. the user wiped the uv tool env). The `existsSync`
 * call itself stays in `provision.ts`; this keeps the *decision* pure & testable.
 *
 * @param cached the path previously persisted in globalState (or `undefined`).
 * @param exists the result of `fs.existsSync(cached)` (false when `cached` is absent).
 */
export function shouldReresolve(cached: string | undefined, exists: boolean): boolean {
    if (cached === undefined || cached.trim().length === 0) return true;
    return !exists;
}
