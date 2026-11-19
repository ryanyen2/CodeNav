/**
 * The daemon is kept running by asking for it repeatedly, not by starting it
 * once. extension.ts and daemon-manager.ts import `vscode` and cannot be
 * loaded here, so these read their source and check that each trigger is
 * wired. A trigger that goes missing shows up to a person only as edits that
 * are accepted and never applied.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const extension = readFileSync(resolve(__dirname, '../../extension.ts'), 'utf8');
const manager = readFileSync(resolve(__dirname, '../../host/daemon-manager.ts'), 'utf8');

describe('the daemon is reconciled', () => {
    it('on every .codoc change', () => {
        expect(extension).toMatch(/state\.onDidChange\(\(\) => reconcileDaemon\(\)\)/);
    });

    it('when the workspace is trusted', () => {
        expect(extension).toMatch(/onDidGrantWorkspaceTrust\(\(\) => reconcileDaemon\(\)\)/);
    });

    it('on a timer, for a window where nothing else happens', () => {
        expect(extension).toMatch(/setInterval\(reconcileDaemon, DAEMON_RECONCILE_MS\)/);
        expect(extension).toMatch(/clearInterval\(daemonTicker\)/);
    });

    it('and by a command, which clears the crash budget first', () => {
        const at = extension.indexOf("registerCommand('codoc.startDaemon'");
        expect(at).toBeGreaterThan(-1);
        expect(extension.slice(at, at + 600)).toMatch(/resetDaemonBackoff\(\)/);
    });
});

describe('a daemon that cannot start', () => {
    it('is not respawned past the crash budget', () => {
        expect(manager).toMatch(/if \(fastExits >= MAX_FAST_EXITS\) return false/);
    });

    it('does not spend the budget by standing down for another daemon', () => {
        expect(manager).toMatch(/code === STAND_DOWN_EXIT/);
    });
});
