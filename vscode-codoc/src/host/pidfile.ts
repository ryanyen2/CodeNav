/**
 * pidfile.ts: `.codoc/watch.pid`, the daemon's claim on a repository.
 *
 * The file holds the daemon's process id as decimal text. The daemon holds an
 * exclusive lock on it for its whole life; a second `codoc watch` finds the
 * lock held and exits with status 3 (`hold_pidfile` and `daemon_running` on the
 * Python side). The lock is the truth, and the file is never removed: removing
 * it while a daemon holds it would let another daemon lock a new file at the
 * same path. The extension only reads the pid, as a hint.
 *
 * No `vscode` import, so vitest can load it.
 */
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
