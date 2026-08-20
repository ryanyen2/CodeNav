/**
 * daemon-manager.ts — vscode-facing `codoc watch` daemon lifecycle (U6).
 *
 * The user never runs `codoc watch` by hand: the extension starts the warm
 * daemon on activation and stops it on deactivation. The daemon is kept warm
 * (not cold-spawned per save) because it holds expensive state — the code index,
 * the embedding model, LanceDB — that would be re-paid on every edit otherwise.
 *
 * Two failure modes this guards against:
 *   • multi-window duplicates — the daemon is single-owner per repo, tracked via
 *     `.codoc/watch.pid`. {@link shouldSpawn} defers when a *live* lock exists.
 *   • orphaned processes — the child is told its parent pid via
 *     `CODOC_WATCH_PARENT_PID`; the Python loop self-exits when that pid dies, a
 *     defense independent of `deactivate()` actually running. {@link stopDaemon}
 *     also SIGTERMs synchronously inside `deactivate`'s limited budget.
 *
 * All pure lock logic (record shape, serialize/parse, stale/should-spawn
 * decisions) lives in `./lockfile`; this file is the vscode + child_process
 * wiring and is intentionally NOT covered by vitest.
 *
 * Security: spawning is gated on Workspace Trust (KTD6) and only for an already
 * initialized repo (a `.codoc/` dir). The child gets an explicit `env`; no
 * untrusted input is interpolated into a shell string (argv-only, shell:false).
 */

import * as vscode from 'vscode';
import * as cp from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as crypto from 'node:crypto';
import {
    WatchLock, parseLock, serializeLock, shouldSpawn,
} from './lockfile';

/** globalState key for this window's stable id (the daemon owner tag). */
const KEY_WINDOW_ID = 'codoc.daemon.windowId';

/** Module-level handle to the daemon we spawned (null when we didn't / it stopped). */
let _child: cp.ChildProcess | undefined;
/** The window id we wrote into the lock — used to only reap a lock we own. */
let _ownerId: string | undefined;
/** The repo's `.codoc` dir for the daemon we own, so `stopDaemon` can find the lock. */
let _codocDir: string | undefined;

// ── Crash-loop backoff ────────────────────────────────────────────────────────
//
// `reconcileDaemon` calls this on a timer, so "start it if it isn't running" is
// asked over and over rather than once. That is what makes the daemon survive a
// missed watcher event — and it is also what would respawn a daemon that dies on
// startup, forever, a few seconds apart, writing the same traceback into the
// output channel until somebody notices.
//
// So a spawn that dies quickly counts against a budget. Past it we stop trying and
// say so once; a spawn that lives longer than the window clears the count, because
// a daemon that ran for a minute and then exited is a different event from one that
// could never start. `startDaemon` called directly (a command, a trust grant) resets
// the budget: that is a person asking, and the answer to a person is to try again.
const FAST_EXIT_MS = 5_000;
const MAX_FAST_EXITS = 3;
/**
 * The daemon's exit status for "another daemon already owns this repo"
 * (`codoc.loop.watch.STAND_DOWN_EXIT`). It is a deliberate stand-down and must never
 * count against the crash budget: it is not a daemon that CANNOT start, it is one
 * that correctly declined to start twice. Counting it spent the whole budget on
 * three harmless races — a window reload, a bounce, two windows on one repo — and
 * left the workspace with no daemon and no retry, which the author sees only as
 * edits that are accepted and then never picked up by any loop.
 */
const STAND_DOWN_EXIT = 3;
let _fastExits = 0;
let _spawnedAt = 0;
let _gaveUpAnnounced = false;

/** Clear the crash-loop budget — for an explicit, person-initiated start. */
export function resetDaemonBackoff(): void {
    _fastExits = 0;
    _gaveUpAnnounced = false;
}

/** True when repeated fast exits have stopped us retrying (reconcile should stand down). */
export function daemonGaveUp(): boolean {
    return _fastExits >= MAX_FAST_EXITS;
}

/** The shared "codoc" OutputChannel (same name as provision.ts reuses). */
let _channel: vscode.OutputChannel | undefined;
function outputChannel(): vscode.OutputChannel {
    if (!_channel) _channel = vscode.window.createOutputChannel('codoc');
    return _channel;
}

/** Stable per-window id, persisted in globalState (minted once, reused after). */
function windowId(context: vscode.ExtensionContext): string {
    let id = context.globalState.get<string>(KEY_WINDOW_ID);
    if (!id) {
        id = crypto.randomUUID();
        void context.globalState.update(KEY_WINDOW_ID, id);
    }
    return id;
}

/** Path to the daemon lock for a `.codoc` dir. */
function lockPath(codocDir: string): string {
    return path.join(codocDir, 'watch.pid');
}

/** Read + parse the existing lock, or `undefined` if absent/unparseable. */
function readLock(codocDir: string): WatchLock | undefined {
    let raw: string | undefined;
    try {
        raw = fs.readFileSync(lockPath(codocDir), 'utf8');
    } catch {
        return undefined; // ENOENT etc. → no lock
    }
    return parseLock(raw);
}

/** Liveness probe used by the pure decisions — `process.kill(pid, 0)` throws if dead. */
function isPidAlive(pid: number): boolean {
    try {
        process.kill(pid, 0); // signal 0 = liveness probe, doesn't actually signal
        return true;
    } catch (e) {
        // EPERM means the process exists but we can't signal it → still alive.
        return (e as NodeJS.ErrnoException).code === 'EPERM';
    }
}

/**
 * Is `pid` actually a `codoc watch` process? Mirrors `codoc.loop.watch._is_codoc_daemon`,
 * and both halves need it for the same reason: a pid is not an identity.
 *
 * Pids are recycled, hardest right after a reboot when the counter restarts low and
 * runs back through the range a stale `watch.pid` from the previous boot is naming.
 * `isPidAlive` alone then reports a live daemon that is really the user's browser —
 * and this side fails WORSE than the Python side, because it counts `EPERM` as alive,
 * so any root-owned process holding the number also wins. The window defers forever,
 * nothing reaps the lock, and the author sees only edits no loop ever picks up.
 *
 * Unknown answers count as NOT a daemon, the same asymmetry the Python side takes: a
 * false negative costs a second daemon, which the loop lock makes safe and the very
 * next lock read resolves; a false positive costs a workspace that never syncs again.
 */
function isCodocDaemon(pid: number): boolean {
    try {
        const out = cp.execFileSync('ps', ['-p', String(pid), '-o', 'command='],
            { encoding: 'utf8', timeout: 5_000 });
        return out.includes('codoc') && out.includes('watch');
    } catch {
        return false;   // ps failed, or the pid is gone between the two calls
    }
}

/** The full test the lock decisions want: a live process that is really our daemon. */
function ownsWarmDaemon(pid: number): boolean {
    return isPidAlive(pid) && isCodocDaemon(pid);
}

/** Write the owner lock for the daemon we just spawned (mirrors the Python format). */
function writeLock(codocDir: string, pid: number, owner: string): void {
    const lock: WatchLock = { pid, owner, startedAt: Date.now() / 1000 };
    try {
        fs.writeFileSync(lockPath(codocDir), serializeLock(lock));
    } catch (e) {
        outputChannel().appendLine(`codoc: could not write watch.pid: ${(e as Error).message}`);
    }
}

/** Best-effort remove the lock — only when it still names a daemon we own. */
function removeOwnedLock(codocDir: string, owner: string): void {
    const lock = readLock(codocDir);
    if (lock && lock.owner !== owner) return; // someone else owns it now — leave it
    try {
        fs.rmSync(lockPath(codocDir));
    } catch {
        // already gone / unwritable — nothing to do
    }
}

/**
 * Start the `codoc watch` daemon for `rootDir`, if appropriate. No-op (returns
 * `false`) when the workspace is untrusted, the repo isn't initialized (no
 * `.codoc/`), we already own a running child, or a *live* daemon lock exists
 * (another window / a manual `codoc watch` owns the warm daemon).
 *
 * On spawn: pipes the daemon's stdout/stderr to the "codoc" OutputChannel and
 * writes an owner lock (pid + this window's id + start time). The child is told
 * our pid via `CODOC_WATCH_PARENT_PID` so it self-exits if this host dies, and
 * our window id via `CODOC_WATCH_OWNER` so the lock it (re)writes stays ours.
 * Deliberately does NOT pass `--auto-realize` (KTD6 — realization stays in the
 * interactive session).
 *
 * @returns `true` if a daemon was spawned by this call, else `false`.
 */
export function startDaemon(
    context: vscode.ExtensionContext,
    codocPath: string,
    rootDir: string,
): boolean {
    if (!vscode.workspace.isTrusted) return false;       // KTD6: trust-gated
    if (_child && _child.exitCode === null) return false; // we already own one

    const codocDir = path.join(rootDir, '.codoc');
    if (!fs.existsSync(codocDir)) return false;           // repo not initialized yet

    if (daemonGaveUp()) return false;                     // crash-looping → stop trying

    const id = windowId(context);
    const existing = readLock(codocDir);
    if (!shouldSpawn(existing, id, ownsWarmDaemon)) return false; // live owner exists → defer

    const channel = outputChannel();
    if (existing) channel.appendLine(`codoc: reaping stale watch.pid (pid ${existing.pid} dead)`);

    const env: NodeJS.ProcessEnv = {
        ...process.env,
        CODOC_WATCH_PARENT_PID: String(process.pid),
        CODOC_WATCH_OWNER: id,
    };

    channel.appendLine(`$ ${codocPath} watch --root ${rootDir}`);
    const child = cp.spawn(codocPath, ['watch', '--root', rootDir], {
        cwd: rootDir,
        env,
        detached: false, // tie the child's lifetime to this host process
        shell: false,
    });

    child.stdout?.on('data', (buf: Buffer) => channel.append(buf.toString()));
    child.stderr?.on('data', (buf: Buffer) => channel.append(buf.toString()));
    child.on('error', err => channel.appendLine(`codoc watch failed to start: ${err.message}`));
    child.on('exit', (code, signal) => {
        channel.appendLine(`codoc watch exited (code ${code ?? '—'}, signal ${signal ?? '—'})`);
        if (code === STAND_DOWN_EXIT) {
            // Someone else owns it. Not our daemon, not a failure, nothing to retry
            // against — the next reconcile will see the live lock and defer quietly.
            _fastExits = 0;
        } else if (Date.now() - _spawnedAt < FAST_EXIT_MS) {
            _fastExits += 1;
            if (_fastExits >= MAX_FAST_EXITS && !_gaveUpAnnounced) {
                _gaveUpAnnounced = true;
                channel.appendLine(
                    `codoc: ${_fastExits} starts in a row exited within ${FAST_EXIT_MS / 1000}s — `
                    + 'not starting it again on its own. Run "codoc: Start the daemon" '
                    + 'once the cause is fixed.');
            }
        } else {
            _fastExits = 0;   // it ran; whatever ended it is not a startup failure
        }
        if (_child === child) {
            _child = undefined;
            if (_codocDir && _ownerId) removeOwnedLock(_codocDir, _ownerId);
        }
    });

    _spawnedAt = Date.now();
    _child = child;
    _ownerId = id;
    _codocDir = codocDir;
    if (child.pid !== undefined) writeLock(codocDir, child.pid, id);
    channel.appendLine(`codoc watch started (pid ${child.pid ?? '?'})`);
    return true;
}

/**
 * Stop the daemon we own, SYNCHRONOUSLY. Sends `SIGTERM` (no async work) so it is
 * safe to call from `deactivate()`, which has a limited budget and cannot await.
 * Best-effort removes our lock. The Python `CODOC_WATCH_PARENT_PID` self-exit is
 * the backstop for the case where `deactivate` never runs (host crash).
 */
export function stopDaemon(): void {
    const child = _child;
    if (child && child.exitCode === null) {
        try {
            child.kill('SIGTERM');
        } catch {
            // already dead / unkillable — nothing to do
        }
    }
    if (_codocDir && _ownerId) removeOwnedLock(_codocDir, _ownerId);
    _child = undefined;
    _ownerId = undefined;
    _codocDir = undefined;
}

/**
 * Reap a stale daemon lock without spawning — for activation-time cleanup before
 * deciding whether to start. Removes `watch.pid` only when it names a dead pid;
 * leaves a live lock alone. Returns `true` if a stale lock was removed.
 */
export function reapStaleLock(rootDir: string): boolean {
    const codocDir = path.join(rootDir, '.codoc');
    const lock = readLock(codocDir);
    if (!lock || ownsWarmDaemon(lock.pid)) return false;
    try {
        fs.rmSync(lockPath(codocDir));
        outputChannel().appendLine(`codoc: reaped stale watch.pid (pid ${lock.pid} dead)`);
        return true;
    } catch {
        return false;
    }
}

/** Whether this window currently owns a running daemon child (for status/tests). */
export function isDaemonOwned(): boolean {
    return _child !== undefined && _child.exitCode === null;
}
