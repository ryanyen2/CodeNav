/**
 * pidfile.ts — `.codoc/watch.pid`, the daemon's claim on a repository.
 *
 * The file holds the daemon's process id as decimal text and nothing else. The
 * daemon writes it when it starts and removes it when it exits; a daemon that
 * finds a live pid there exits with status 3 instead of starting (`codoc
 * watch`, `write_pidfile` and `daemon_running` on the Python side). The
 * extension never writes the file. It reads it to decide whether a daemon is
 * running, and removes one that names a process which is not a daemon.
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

export function removePidfile(codocDir: string): void {
    fs.rmSync(pidfilePath(codocDir), { force: true });
}
