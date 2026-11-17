/**
 * daemon-manager.ts — starts and stops `codoc watch` for a repository.
 *
 * One daemon serves a repository, whoever started it: this window, another
 * window, `codoc serve`, or a person in a terminal. `startDaemon` is safe to
 * call again and again, which is how extension.ts uses it: it does nothing
 * while this window's child is running or while the pidfile names a live
 * daemon (host/pidfile.ts). The daemon itself is the final arbiter. One that
 * starts beside another exits with status 3.
 *
 * The child is spawned from an argument vector with no shell, only in a
 * trusted workspace, and its output goes to the "codoc" output channel. It is
 * not detached, and `stopDaemon` sends SIGTERM synchronously so it can run
 * inside `deactivate`.
 *
 * A daemon that exits within FAST_EXIT_MS of starting counts against a budget
 * of MAX_FAST_EXITS. Past the budget `startDaemon` stops trying, says so once,
 * and waits for `resetDaemonBackoff`, so a daemon that cannot start is not
 * respawned on every reconcile tick. A stand-down (status 3) is not a failure
 * and does not count.
 */
import * as vscode from 'vscode';
import * as cp from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

import { output } from './output';
import { readPid, removePidfile } from './pidfile';

const FAST_EXIT_MS = 5_000;
const MAX_FAST_EXITS = 3;
/** The daemon's exit status for "another daemon already watches this repository". */
const STAND_DOWN_EXIT = 3;

let child: cp.ChildProcess | undefined;
let spawnedAt = 0;
let fastExits = 0;
let gaveUpAnnounced = false;

/** Clear the crash budget. For a start a person asked for. */
export function resetDaemonBackoff(): void {
    fastExits = 0;
    gaveUpAnnounced = false;
}

/**
 * Whether `pid` is a running `codoc watch`. Liveness alone is not enough where
 * the command line can be read: process ids are reused, so a pidfile left by a
 * previous boot can name the person's browser. Windows has no `ps`, and there
 * liveness is the whole test, as it is for the daemon's own check.
 */
function isLiveDaemon(pid: number): boolean {
    try {
        process.kill(pid, 0);
    } catch (e) {
        // EPERM: the process exists and belongs to someone else.
        if ((e as NodeJS.ErrnoException).code !== 'EPERM') return false;
    }
    if (process.platform === 'win32') return true;
    try {
        const command = cp.execFileSync('ps', ['-p', String(pid), '-o', 'command='], { encoding: 'utf8', timeout: 5_000 });
        return command.includes('codoc') && command.includes('watch');
    } catch {
        return false;
    }
}

/**
 * Start the daemon for `rootDir` unless one is already running or it may not
 * run here. Returns true when this call spawned one.
 */
export function startDaemon(codocPath: string, rootDir: string): boolean {
    if (!vscode.workspace.isTrusted) return false;
    if (child && child.exitCode === null) return false;
    const codocDir = path.join(rootDir, '.codoc');
    if (!fs.existsSync(codocDir)) return false;
    if (fastExits >= MAX_FAST_EXITS) return false;

    const log = output();
    const pid = readPid(codocDir);
    if (pid !== undefined) {
        if (isLiveDaemon(pid)) return false;
        // The file names a process that is not a daemon. Left in place, the
        // daemon about to start could take it for a live one and stand down.
        removePidfile(codocDir);
        log.appendLine(`codoc: removed a stale watch.pid (pid ${pid})`);
    }

    log.appendLine(`$ ${codocPath} watch --root ${rootDir}`);
    const proc = cp.spawn(codocPath, ['watch', '--root', rootDir], { cwd: rootDir, shell: false });
    proc.stdout?.on('data', (buf: Buffer) => log.append(buf.toString()));
    proc.stderr?.on('data', (buf: Buffer) => log.append(buf.toString()));
    proc.on('error', err => log.appendLine(`codoc watch failed to start: ${err.message}`));
    proc.on('exit', (code, signal) => {
        log.appendLine(`codoc watch exited (code ${code ?? '—'}, signal ${signal ?? '—'})`);
        if (code === STAND_DOWN_EXIT || Date.now() - spawnedAt >= FAST_EXIT_MS) {
            fastExits = 0;
        } else {
            fastExits += 1;
            if (fastExits >= MAX_FAST_EXITS && !gaveUpAnnounced) {
                gaveUpAnnounced = true;
                log.appendLine(`codoc: ${fastExits} starts in a row exited within ${FAST_EXIT_MS / 1000}s. `
                    + 'It will not be started again on its own. Run "codoc: Start the daemon" once the cause is fixed.');
            }
        }
        if (child === proc) child = undefined;
    });

    spawnedAt = Date.now();
    child = proc;
    log.appendLine(`codoc watch started (pid ${proc.pid ?? '?'})`);
    return true;
}

/** Stop the daemon this window started, if it is still running. */
export function stopDaemon(): void {
    if (child && child.exitCode === null) {
        try {
            child.kill('SIGTERM');
        } catch {
            // already gone
        }
    }
    child = undefined;
}
