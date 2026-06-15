/**
 * lockfile.ts — PURE, vscode-free `.codoc/watch.pid` logic (vitest-testable).
 *
 * The codoc watch daemon holds expensive warm state (the code index, the
 * embedding model, LanceDB), so it runs once per repo and is *single-owner*: the
 * first VS Code window to see an initialized repo spawns it; other windows defer.
 * Ownership is recorded in `.codoc/watch.pid` as JSON `{pid, owner, started_at}`,
 * written identically by the Python daemon (`write_pidfile`) and parsed here.
 *
 * This module is everything that can be decided without `vscode` or a process
 * handle — the lock record shape, its serialize/parse, and the two decisions the
 * extension makes:
 *   • {@link isStaleLock}  — is an existing lock dead (its pid no longer alive)?
 *   • {@link shouldSpawn}  — given an existing lock, should *this* window spawn?
 *
 * The vscode wiring (spawning, OutputChannel, globalState window id) lives in
 * `./daemon-manager`, which imports these. `vitest.config.mjs` runs
 * `src/test/**` against this file, so it must NOT import 'vscode'.
 */

/**
 * The on-disk shape of `.codoc/watch.pid`. The Python daemon writes the same
 * JSON; a legacy bare-int file (pre-U6) parses to `{pid, owner: '', started_at: 0}`.
 */
export interface WatchLock {
    /** OS process id of the running `codoc watch` daemon. */
    readonly pid: number;
    /** The VS Code window that spawned it (empty for a plain CLI `codoc watch`). */
    readonly owner: string;
    /** Unix epoch seconds when the daemon started (0 for a legacy bare-int file). */
    readonly startedAt: number;
}

/**
 * Serialize a lock record to the exact JSON shape the Python daemon writes
 * (`{pid, owner, started_at}`) so either side can author the file.
 */
export function serializeLock(lock: WatchLock): string {
    return JSON.stringify({ pid: lock.pid, owner: lock.owner, started_at: lock.startedAt });
}

/**
 * Parse `.codoc/watch.pid` contents into a {@link WatchLock}, tolerating both the
 * JSON `{pid, owner, started_at}` form and a legacy bare-int pidfile. Returns
 * `undefined` for missing / empty / unparseable / pid-less content so callers
 * degrade to "no owner" rather than throwing.
 *
 * @param raw the file contents (or `undefined` when the file is absent).
 */
export function parseLock(raw: string | undefined): WatchLock | undefined {
    if (raw === undefined) return undefined;
    const text = raw.trim();
    if (text.length === 0) return undefined;

    // JSON `{pid, owner, started_at}` (current format, both Python & TS writers).
    try {
        const obj = JSON.parse(text) as { pid?: unknown; owner?: unknown; started_at?: unknown };
        const pid = toPid(obj.pid);
        if (pid !== undefined) {
            return {
                pid,
                owner: typeof obj.owner === 'string' ? obj.owner : '',
                startedAt: typeof obj.started_at === 'number' ? obj.started_at : 0,
            };
        }
    } catch {
        // not JSON — fall through to the legacy bare-int form.
    }

    // Legacy bare-int pidfile (pre-U6 / the Python Stop-hook tests).
    const bare = toPid(text);
    if (bare !== undefined) return { pid: bare, owner: '', startedAt: 0 };
    return undefined;
}

/** Coerce a value to a positive integer pid, or `undefined` if it isn't one. */
function toPid(value: unknown): number | undefined {
    const n = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : NaN;
    return Number.isInteger(n) && n > 0 ? n : undefined;
}

/**
 * Decide whether an existing lock is stale (the daemon it names is dead). The
 * actual liveness probe stays in `daemon-manager.ts` (it needs `process.kill`);
 * this keeps the *decision* pure and testable by injecting the probe.
 *
 * @param lock the parsed existing lock.
 * @param isPidAlive a liveness predicate (e.g. `pid => process.kill(pid, 0)`).
 */
export function isStaleLock(lock: WatchLock, isPidAlive: (pid: number) => boolean): boolean {
    return !isPidAlive(lock.pid);
}

/**
 * Decide whether *this* window should spawn the daemon. Spawn when there is no
 * lock, or the existing lock is stale (its pid is dead → safe to reap and take
 * over). Do NOT spawn when a *live* lock exists — another window (or a manual
 * `codoc watch`) already owns the warm daemon, regardless of who owns it. This is
 * what keeps a single daemon per repo across multiple VS Code windows.
 *
 * @param existing the parsed existing lock, or `undefined` if `watch.pid` is absent.
 * @param myWindowId this window's id (reserved for future owner-aware policy; the
 *   single-owner rule is window-agnostic by design — any live daemon wins).
 * @param isPidAlive a liveness predicate for the existing lock's pid.
 */
export function shouldSpawn(
    existing: WatchLock | undefined,
    myWindowId: string,
    isPidAlive: (pid: number) => boolean,
): boolean {
    void myWindowId;
    if (existing === undefined) return true;       // no owner → spawn
    return isStaleLock(existing, isPidAlive);       // dead owner → reap & spawn; live → defer
}
