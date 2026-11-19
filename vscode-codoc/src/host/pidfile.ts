/**
 * pidfile.ts: `.codoc/watch.pid`, the daemon's claim on a repository.
 *
 * The file holds the daemon's process id as decimal text. The daemon holds an
 * exclusive lock on it for its whole life; a second `codoc watch` finds the
 * lock held and exits with status 3 (`hold_pidfile` and `daemon_running` on the
 * Python side). The lock is the truth, and the file is never removed: removing
 * it while a daemon holds it would let another daemon lock a new file at the
 * same path. Node cannot take that lock, so the extension reads the pid as a
 * hint (`daemonRunning`): it decides what the status bar says and whether to
 * spawn a daemon, and a daemon spawned beside a live one stands down.
 *
 * No `vscode` import, so vitest can load it.
 */
import * as cp from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

function pidfilePath(codocDir: string): string {
    return path.join(codocDir, 'watch.pid');
}

/** The pid a pidfile's text names, or undefined when it names none. */
export function parsePid(text: string): number | undefined {
    const t = text.trim();
    return /^[1-9]\d*$/.test(t) ? Number(t) : undefined;
}

/** The pid in a repository's pidfile, or undefined when the file is absent or unreadable. */
export function readPid(codocDir: string): number | undefined {
    try {
        return parsePid(fs.readFileSync(pidfilePath(codocDir), 'utf8'));
    } catch {
        return undefined;
    }
}

/**
 * Whether the pidfile names a running `codoc watch`. The process must exist
 * (EPERM means it exists and belongs to someone else) and its command line
 * must be a `codoc watch`, so a pid left by a killed daemon, or one another
 * program has since been given, reads as no daemon. Windows has no `ps`, so
 * there the process existing is enough.
 */
export function daemonRunning(codocDir: string): boolean {
    const pid = readPid(codocDir);
    if (pid === undefined) return false;
    try {
        process.kill(pid, 0);
    } catch (e) {
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
