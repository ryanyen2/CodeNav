/**
 * daemon-manager.test.ts — the PURE daemon-lock logic (U6).
 *
 * Imports ONLY from `../daemon/lockfile` (never `vscode`, never
 * `../daemon/daemon-manager`) so it runs under `vitest.config.mjs` ("modules
 * under test must not import 'vscode'"). These guard the single-owner /
 * stale-reap decisions the extension makes against `.codoc/watch.pid`:
 *   • serialize → parse round-trips the lock record (and matches the Python JSON);
 *   • parse tolerates a legacy bare-int pidfile and junk;
 *   • isStaleLock reflects the injected liveness probe;
 *   • shouldSpawn: no lock → spawn, stale lock → spawn, live lock → defer.
 */
import { describe, it, expect } from 'vitest';
import {
    WatchLock, isStaleLock, parseLock, serializeLock, shouldSpawn,
} from '../daemon/lockfile';

const alive = (): boolean => true;
const dead = (): boolean => false;

describe('serializeLock / parseLock round-trip', () => {
    it('round-trips a full lock record', () => {
        const lock: WatchLock = { pid: 4242, owner: 'window-abc', startedAt: 1718000000.5 };
        const back = parseLock(serializeLock(lock));
        expect(back).toEqual(lock);
    });

    it('serializes to the Python {pid, owner, started_at} shape', () => {
        const json = serializeLock({ pid: 7, owner: 'w', startedAt: 12 });
        const obj = JSON.parse(json);
        expect(obj).toEqual({ pid: 7, owner: 'w', started_at: 12 });
    });
});

describe('parseLock', () => {
    it('parses the JSON {pid, owner, started_at} form', () => {
        const lock = parseLock('{"pid": 999, "owner": "win-1", "started_at": 1700000000}');
        expect(lock).toEqual({ pid: 999, owner: 'win-1', startedAt: 1700000000 });
    });

    it('parses a legacy bare-int pidfile (owner empty, startedAt 0)', () => {
        expect(parseLock('12345')).toEqual({ pid: 12345, owner: '', startedAt: 0 });
    });

    it('trims surrounding whitespace', () => {
        expect(parseLock('  6789\n')).toEqual({ pid: 6789, owner: '', startedAt: 0 });
    });

    it('defaults a JSON file missing owner/started_at', () => {
        expect(parseLock('{"pid": 5}')).toEqual({ pid: 5, owner: '', startedAt: 0 });
    });

    it('returns undefined for absent / empty content', () => {
        expect(parseLock(undefined)).toBeUndefined();
        expect(parseLock('')).toBeUndefined();
        expect(parseLock('   \n')).toBeUndefined();
    });

    it('returns undefined for junk and for a pid-less JSON object', () => {
        expect(parseLock('{ not json')).toBeUndefined();
        expect(parseLock('{"owner": "w"}')).toBeUndefined();
        expect(parseLock('not-a-number')).toBeUndefined();
        expect(parseLock('{"pid": 0}')).toBeUndefined();   // 0 is not a valid pid
        expect(parseLock('{"pid": -3}')).toBeUndefined();  // negative is not valid
    });
});

describe('isStaleLock', () => {
    const lock: WatchLock = { pid: 4242, owner: 'w', startedAt: 0 };

    it('is true when the pid is dead', () => {
        expect(isStaleLock(lock, dead)).toBe(true);
    });

    it('is false when the pid is alive', () => {
        expect(isStaleLock(lock, alive)).toBe(false);
    });
});

describe('shouldSpawn', () => {
    const live: WatchLock = { pid: 100, owner: 'other-window', startedAt: 0 };
    const stale: WatchLock = { pid: 200, owner: 'other-window', startedAt: 0 };

    it('spawns when there is no lock', () => {
        expect(shouldSpawn(undefined, 'me', dead)).toBe(true);
        expect(shouldSpawn(undefined, 'me', alive)).toBe(true);
    });

    it('spawns when the existing lock is stale (dead pid → reap & take over)', () => {
        expect(shouldSpawn(stale, 'me', dead)).toBe(true);
    });

    it('defers when a live lock owned by another window exists (single-owner)', () => {
        expect(shouldSpawn(live, 'me', alive)).toBe(false);
    });

    it('defers even when the live lock is owned by us (any live daemon wins)', () => {
        expect(shouldSpawn({ pid: 1, owner: 'me', startedAt: 0 }, 'me', alive)).toBe(false);
    });

    it('defers to a live hub-owned daemon (owner "serve" — the codoc serve hub owns it)', () => {
        // The `codoc serve` hub spawns the daemon with CODOC_WATCH_OWNER="serve",
        // so its watch.pid carries owner "serve". A window opening the repo while
        // the hub runs must defer, not double-spawn (plan U1).
        expect(shouldSpawn({ pid: 55, owner: 'serve', startedAt: 0 }, 'me', alive)).toBe(false);
    });
});
