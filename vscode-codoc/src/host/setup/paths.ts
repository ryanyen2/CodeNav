/**
 * paths.ts — the commands and paths provisioning uses, as plain values.
 *
 * Everything setup/provision.ts decides without running a process is here:
 * the `uv` argument vectors, the installer command per platform, where the
 * installer puts `uv`, where `uv` puts codoc's console scripts, which file in
 * `bundled/` is the wheel, and whether a cached path is still good.
 *
 * No `vscode` import and no process spawning, so vitest can load it.
 */
import * as path from 'node:path';

/** The Python version codoc's environment is pinned to. */
export const CODOC_PYTHON_VERSION = '3.11';

/** The paths of codoc's two console scripts. */
export interface CodocExecutables {
    /** The `codoc` CLI. */
    readonly codoc: string;
    /** The `codoc-mcp` server. */
    readonly codocMcp: string;
}

/** A command to run. `shell` is true only for the uv installer, which is a pipeline. */
export interface CommandSpec {
    readonly command: string;
    readonly args: readonly string[];
    readonly shell: boolean;
}

function exeSuffix(platform: NodeJS.Platform): string {
    return platform === 'win32' ? '.exe' : '';
}

/** The script paths inside the directory `uv tool dir --bin` printed. */
export function executablePaths(binDir: string, platform: NodeJS.Platform): CodocExecutables {
    const dir = binDir.trim();
    if (dir.length === 0) {
        throw new Error('uv tool dir --bin returned an empty path, so the codoc executables cannot be found.');
    }
    const suffix = exeSuffix(platform);
    return { codoc: path.join(dir, `codoc${suffix}`), codocMcp: path.join(dir, `codoc-mcp${suffix}`) };
}

/** uv's standalone installer. Both forms are pipelines of constant strings, so
 *  a shell runs them and nothing from the workspace is interpolated. */
export function uvInstallerCommand(platform: NodeJS.Platform): CommandSpec {
    if (platform === 'win32') {
        return {
            command: 'powershell',
            args: ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', 'irm https://astral.sh/uv/install.ps1 | iex'],
            shell: true,
        };
    }
    return { command: 'sh', args: ['-c', 'curl -LsSf https://astral.sh/uv/install.sh | sh'], shell: true };
}

/** Where the standalone installer puts `uv` by default. Used directly after an
 *  install, because the extension host's PATH does not change until restart. */
export function uvInstalledPath(home: string, platform: NodeJS.Platform): string {
    return path.join(home, '.local', 'bin', `uv${exeSuffix(platform)}`);
}

export function uvPythonInstallArgs(version: string = CODOC_PYTHON_VERSION): string[] {
    return ['python', 'install', version];
}

/**
 * `uv tool install` for the bundled wheel. `--with claude-agent-sdk` puts the
 * SDK in the same environment, which `codoc realize` needs for its SDK engine
 * and a wheel path cannot ask for as an extra. `--reinstall` is required
 * because the wheel is rebuilt without a version bump, and uv skips a version
 * it already has.
 */
export function uvToolInstallArgs(wheelPath: string, version: string = CODOC_PYTHON_VERSION): string[] {
    if (wheelPath.trim().length === 0) {
        throw new Error('uvToolInstallArgs: a wheel path is required.');
    }
    return ['tool', 'install', '--reinstall', '--python', version, '--with', 'claude-agent-sdk', wheelPath];
}

export function uvToolDirBinArgs(): string[] {
    return ['tool', 'dir', '--bin'];
}

export function uvVersionArgs(): string[] {
    return ['--version'];
}

/** The one `codoc-*.whl` in a listing of `bundled/`. None, or more than one, is a packaging error. */
export function findBundledWheel(listing: readonly string[]): string {
    const wheels = listing.filter(name => name.startsWith('codoc') && name.endsWith('.whl'));
    if (wheels.length === 0) {
        throw new Error('No bundled codoc wheel found (expected exactly one codoc-*.whl). Run `npm run bundle-wheel`.');
    }
    if (wheels.length > 1) {
        throw new Error(`Multiple bundled codoc wheels found (${wheels.join(', ')}); expected exactly one.`);
    }
    return wheels[0];
}

/** Whether a cached executable path must be resolved again: there is none, or
 *  the file it names is gone. `exists` is `fs.existsSync(cached)`. */
export function shouldReresolve(cached: string | undefined, exists: boolean): boolean {
    if (cached === undefined || cached.trim().length === 0) return true;
    return !exists;
}
