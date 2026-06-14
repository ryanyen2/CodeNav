/**
 * provision.test.ts — the PURE provisioning helpers (U2).
 *
 * Imports ONLY from `../setup/paths` (never `vscode`, never `../setup/provision`)
 * so it runs under `vitest.config.mjs` ("modules under test must not import 'vscode'").
 * These guard the pieces of provisioning that have no host dependency:
 *   • executable-path layout per platform (bare names vs `.exe`);
 *   • the uv standalone-installer command per platform;
 *   • the uv argv builders (python install / tool install / dir --bin);
 *   • the bundled-wheel locator (exactly one wheel);
 *   • the stale-cache re-resolve decision.
 */
import { describe, it, expect } from 'vitest';
import * as path from 'node:path';
import {
    CODOC_PYTHON_VERSION,
    executablePaths, findBundledWheel, shouldReresolve, uvInstalledPath,
    uvInstallerCommand, uvPythonInstallArgs, uvToolDirBinArgs, uvToolInstallArgs,
    uvVersionArgs,
} from '../setup/paths';

describe('executablePaths', () => {
    it('joins bare codoc / codoc-mcp under binDir on darwin (no extension)', () => {
        const binDir = '/Users/me/.local/share/uv/tools/bin';
        const execs = executablePaths(binDir, 'darwin');
        expect(execs.codoc).toBe(path.join(binDir, 'codoc'));
        expect(execs.codocMcp).toBe(path.join(binDir, 'codoc-mcp'));
    });

    it('joins bare names on linux too', () => {
        const execs = executablePaths('/home/me/.local/bin', 'linux');
        expect(execs.codoc).toBe(path.join('/home/me/.local/bin', 'codoc'));
        expect(execs.codocMcp).toBe(path.join('/home/me/.local/bin', 'codoc-mcp'));
    });

    it('appends .exe on win32', () => {
        const binDir = 'C:\\Users\\me\\AppData\\Roaming\\uv\\tools\\bin';
        const execs = executablePaths(binDir, 'win32');
        expect(execs.codoc).toBe(path.join(binDir, 'codoc.exe'));
        expect(execs.codocMcp).toBe(path.join(binDir, 'codoc-mcp.exe'));
    });

    it('trims surrounding whitespace/newlines from the uv stdout', () => {
        const execs = executablePaths('  /opt/bin\n', 'darwin');
        expect(execs.codoc).toBe(path.join('/opt/bin', 'codoc'));
    });

    it('throws on empty bin dir', () => {
        expect(() => executablePaths('   ', 'darwin')).toThrow(/empty path/);
    });
});

describe('uvInstallerCommand', () => {
    it('produces the curl | sh form on darwin', () => {
        const spec = uvInstallerCommand('darwin');
        expect(spec.command).toBe('sh');
        expect(spec.shell).toBe(true);
        const joined = spec.args.join(' ');
        expect(joined).toContain('curl -LsSf https://astral.sh/uv/install.sh | sh');
    });

    it('produces the curl | sh form on linux', () => {
        const spec = uvInstallerCommand('linux');
        expect(spec.command).toBe('sh');
        expect(spec.args.join(' ')).toContain('https://astral.sh/uv/install.sh');
    });

    it('produces the PowerShell irm | iex form on win32', () => {
        const spec = uvInstallerCommand('win32');
        expect(spec.command).toBe('powershell');
        expect(spec.shell).toBe(true);
        const joined = spec.args.join(' ');
        expect(joined).toContain('irm https://astral.sh/uv/install.ps1 | iex');
    });
});

describe('uvInstalledPath', () => {
    it('is ~/.local/bin/uv on darwin/linux', () => {
        expect(uvInstalledPath('/home/me', 'linux')).toBe(path.join('/home/me', '.local', 'bin', 'uv'));
    });
    it('is %USERPROFILE%\\.local\\bin\\uv.exe on win32', () => {
        expect(uvInstalledPath('C:\\Users\\me', 'win32')).toBe(path.join('C:\\Users\\me', '.local', 'bin', 'uv.exe'));
    });
});

describe('uv argv builders', () => {
    it('uvVersionArgs is the bare --version probe', () => {
        expect(uvVersionArgs()).toEqual(['--version']);
    });

    it('uvPythonInstallArgs pins 3.11 by default', () => {
        expect(uvPythonInstallArgs()).toEqual(['python', 'install', '3.11']);
        expect(uvPythonInstallArgs()).toContain(CODOC_PYTHON_VERSION);
    });

    it('uvPythonInstallArgs honours an explicit version', () => {
        expect(uvPythonInstallArgs('3.12')).toEqual(['python', 'install', '3.12']);
    });

    it('uvToolInstallArgs includes --python, 3.11, the SDK extra, and the wheel path', () => {
        const wheel = '/ext/bundled/codoc-0.1.1-py3-none-any.whl';
        const args = uvToolInstallArgs(wheel);
        expect(args).toEqual(['tool', 'install', '--python', '3.11', '--with', 'claude-agent-sdk', wheel]);
        expect(args).toContain('--python');
        expect(args).toContain('3.11');
        expect(args).toContain(wheel);
        // wheel must be the final positional arg (uv flags precede it)
        expect(args[args.length - 1]).toBe(wheel);
    });

    it('uvToolInstallArgs throws on a blank wheel path', () => {
        expect(() => uvToolInstallArgs('   ')).toThrow(/wheel path is required/);
    });

    it('uvToolDirBinArgs queries the bin dir', () => {
        expect(uvToolDirBinArgs()).toEqual(['tool', 'dir', '--bin']);
    });
});

describe('findBundledWheel', () => {
    it('returns the single codoc wheel from a directory listing', () => {
        const listing = ['.gitkeep', 'codoc-0.1.1-py3-none-any.whl', 'README.md'];
        expect(findBundledWheel(listing)).toBe('codoc-0.1.1-py3-none-any.whl');
    });

    it('throws a clear error when no wheel is present', () => {
        expect(() => findBundledWheel(['.gitkeep'])).toThrow(/No bundled codoc wheel/);
    });

    it('throws a clear error when multiple wheels are present', () => {
        const listing = ['codoc-0.1.0-py3-none-any.whl', 'codoc-0.1.1-py3-none-any.whl'];
        expect(() => findBundledWheel(listing)).toThrow(/Multiple bundled codoc wheels/);
    });

    it('ignores non-codoc .whl files', () => {
        const listing = ['torch-2.0-cp311.whl', 'codoc-0.1.1-py3-none-any.whl'];
        expect(findBundledWheel(listing)).toBe('codoc-0.1.1-py3-none-any.whl');
    });
});

describe('shouldReresolve', () => {
    it('re-resolves when there is no cached value', () => {
        expect(shouldReresolve(undefined, false)).toBe(true);
        expect(shouldReresolve('', false)).toBe(true);
    });

    it('re-resolves when the cached path no longer exists (existsSync=false)', () => {
        expect(shouldReresolve('/old/bin/codoc', false)).toBe(true);
    });

    it('keeps the cache when the cached path still exists', () => {
        expect(shouldReresolve('/cur/bin/codoc', true)).toBe(false);
    });
});
