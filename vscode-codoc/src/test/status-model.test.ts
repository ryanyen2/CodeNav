import { describe, it, expect } from 'vitest';
import { leaseStatus, realizeQueueSize, REALIZING_LEASE_MS, DisplayStatus, daemonUnresponsive, HOST_LOG_GRACE_MS } from '../state/status-model';

const NOW = 1_000_000_000_000;

function realizing(detail = 'implementing 1/2: X'): DisplayStatus {
    return { state: 'realizing', pending: 1, detail };
}

describe('realizeQueueSize', () => {
    it('is 0 for empty / whitespace-only text', () => {
        expect(realizeQueueSize('')).toBe(0);
        expect(realizeQueueSize('   \n\t')).toBe(0);
    });

    it('counts one per ### directive heading', () => {
        expect(realizeQueueSize('preamble\n\n### 1. NEW\n\n### 2. RETIRE\n')).toBe(2);
    });

    it('floors a non-empty queue with no headings to 1 (parity with Python)', () => {
        expect(realizeQueueSize('some queued prose without headings')).toBe(1);
    });
});

describe('leaseStatus', () => {
    it('returns non-realizing states verbatim', () => {
        const s: DisplayStatus = { state: 'awaiting_impl', pending: 3, detail: 'run /codoc:sync' };
        expect(leaseStatus(s, NOW, 3, NOW)).toEqual(s);
    });

    it('preserves a fresh realizing pass (written within the lease window)', () => {
        const s = realizing();
        // written 10s ago, well under the 300s lease
        expect(leaseStatus(s, NOW - 10_000, 2, NOW)).toEqual(s);
    });

    it('decays a stale realizing pass with a queue → awaiting_impl', () => {
        const out = leaseStatus(realizing(), NOW - REALIZING_LEASE_MS - 1, 2, NOW);
        expect(out.state).toBe('awaiting_impl');
        expect(out.pending).toBe(2);
        expect(out.detail).toContain('run /codoc:sync');
    });

    it('decays a stale realizing pass with no queue → in_sync', () => {
        const out = leaseStatus(realizing(), NOW - REALIZING_LEASE_MS - 1, 0, NOW);
        expect(out).toEqual({ state: 'in_sync', pending: 0, detail: '' });
    });

    it('trusts the raw state when no mtime lease info is available', () => {
        const s = realizing();
        expect(leaseStatus(s, undefined, 2, NOW)).toEqual(s);
    });

    it('treats the exact lease boundary as still fresh', () => {
        const s = realizing();
        expect(leaseStatus(s, NOW - REALIZING_LEASE_MS, 2, NOW)).toEqual(s);
    });
});

describe('daemon liveness, read off the append log', () => {
    // The state a pilot sat in for a session: daemon never started, their edit in
    // edits.host.jsonl, and the pill reading the ARCHIVE's status.json — frozen at
    // in_sync on the machine that built it. The one honest signal was the append
    // log nobody was consuming.
    const NOW = 1_700_000_000_000;

    it('an empty or absent log is not a dead daemon', () => {
        expect(daemonUnresponsive(0, NOW - 60_000, NOW)).toBe(false);
        expect(daemonUnresponsive(0, undefined, NOW)).toBe(false);
        expect(daemonUnresponsive(120, undefined, NOW)).toBe(false);
    });

    it('a fresh log is a daemon mid-pass, not a dead one', () => {
        // The daemon merges at the top of every Loop B pass; a log a few seconds
        // old is the happy path in flight.
        expect(daemonUnresponsive(120, NOW - 3_000, NOW)).toBe(false);
        expect(daemonUnresponsive(120, NOW - HOST_LOG_GRACE_MS, NOW)).toBe(false);
    });

    it('a log past the grace means nothing is eating it', () => {
        expect(daemonUnresponsive(120, NOW - HOST_LOG_GRACE_MS - 1, NOW)).toBe(true);
        expect(daemonUnresponsive(1, NOW - 3_600_000, NOW)).toBe(true);
    });
});
