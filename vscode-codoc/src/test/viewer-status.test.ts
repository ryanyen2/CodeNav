/**
 * viewer-status.test.ts — the hub client says what will happen to your edit.
 *
 * The defect these pin: the browser client had no idea what it was allowed to do,
 * so it drew the maintainer's affordances for everyone, a read collaborator's
 * settle came back 403, and the outbox dropped it (correctly — a capability you
 * lack never succeeds on retry) with nobody told. The prose stayed on screen
 * looking saved until the next projection replaced it.
 */
import { describe, it, expect, vi } from 'vitest';
import {
    describe as describeDelivery, roleLabel, roleHint, rejectionMessage,
    noticeFor, type ViewerInfo,
} from '../webview/viewer-status';
import type { Delivery } from '../webview/host-bridge';

const READER: ViewerInfo = { capability: 'none', login: 'guest', canSuggest: false, canHandOff: false };
const CONTRIB: ViewerInfo = { capability: 'suggest', login: 'ada', canSuggest: true, canHandOff: false };
const MAINT: ViewerInfo = { capability: 'handoff', login: 'grace', canSuggest: true, canHandOff: true };

describe('the role a viewer is told they have', () => {
    it('names the consequence, not the GitHub permission', () => {
        // "read collaborator" is your permission; "Suggesting" is what happens to
        // your words. Only the second is actionable while you are typing.
        expect(roleLabel(CONTRIB)).toBe('Suggesting');
        expect(roleLabel(MAINT)).toBe('Editing');
        expect(roleLabel(READER)).toBe('Read only');
        expect(roleLabel(undefined)).toBe('');   // VS Code: nothing to say
    });

    it('explains where a suggestion goes, since that is the surprising part', () => {
        expect(roleHint(CONTRIB)).toMatch(/maintainer/i);
        expect(roleHint(MAINT)).toMatch(/agent/i);
        expect(roleHint(READER)).toMatch(/sign in/i);
    });
});

describe('what the delivery line says', () => {
    it('says nothing at all when everything has landed', () => {
        // A permanent "connected" badge is chrome people stop seeing, which makes
        // it useless on the one day it matters.
        expect(describeDelivery({ state: 'live', queued: 0 })).toBe('');
        expect(describeDelivery(undefined)).toBe('');
    });

    it('distinguishes in-flight from stuck, and counts what is at stake', () => {
        expect(describeDelivery({ state: 'queued', queued: 1 })).toBe('Saving 1 change…');
        expect(describeDelivery({ state: 'queued', queued: 4 })).toBe('Saving 4 changes…');
        expect(describeDelivery({ state: 'offline', queued: 3 })).toBe('Offline — 3 changes waiting');
    });
});

describe('what a refusal says', () => {
    it('tells a reader the edit was not saved, and why they could not save it', () => {
        const msg = rejectionMessage(403, READER);
        expect(msg).toMatch(/write access/i);
        expect(msg).toMatch(/not saved/i);   // the load-bearing half
    });

    it('distinguishes an expired session from a missing permission', () => {
        expect(rejectionMessage(401, CONTRIB)).toMatch(/sign in again/i);
        expect(rejectionMessage(403, CONTRIB)).not.toMatch(/sign in again/i);
    });

    it('still says the edit was lost when the reason is unrecognised', () => {
        // The one thing that must never be silent, whatever the status code.
        expect(rejectionMessage(418, MAINT)).toMatch(/not saved/i);
    });
});

describe('announcing a refusal exactly once', () => {
    // The DOM shell (`mountViewerStatus`) is a thin painter over these; the suite
    // runs node-env with no jsdom by design, so the logic lives out here where it
    // can be driven directly — the same split the decoration modules use.
    const rejected = (status: number, kind: string): Delivery => ({
        state: 'live', queued: 0, rejected: { msg: { kind } as never, status },
    });

    it('says nothing when nothing was refused', () => {
        expect(noticeFor({ state: 'live', queued: 0 }, undefined, CONTRIB)).toBeNull();
    });

    it('announces a refusal the caller has not seen', () => {
        const n = noticeFor(rejected(403, 'doc-settle'), undefined, READER);
        expect(n?.text).toMatch(/write access/i);
    });

    it('stays silent on the SAME refusal, however many times it is reported', () => {
        // The bridge reports the last rejection as STATE, so it is present on every
        // subsequent delivery change. Re-announcing would turn one refusal into a
        // notice on every character the user types afterwards.
        const d = rejected(403, 'doc-settle');
        const first = noticeFor(d, undefined, READER)!;
        expect(noticeFor(d, first.key, READER)).toBeNull();
        expect(noticeFor({ ...d, state: 'queued', queued: 2 }, first.key, READER)).toBeNull();
    });

    it('announces a DIFFERENT refusal even after an earlier one was shown', () => {
        const first = noticeFor(rejected(403, 'a'), undefined, CONTRIB)!;
        const second = noticeFor(rejected(401, 'b'), first.key, CONTRIB);
        expect(second?.text).toMatch(/sign in again/i);
    });
});

describe('the anti-vacuity floor', () => {
    it('a refused command really is dropped from the outbox, not retried', async () => {
        // This is WHY the notice must exist: nothing else will ever mention it
        // again. Drives the real bridge with a hub that answers 403.
        const { createNetworkBridge } = await import('../webview/host-bridge');
        const fetchImpl = vi.fn(async () => ({ ok: false, status: 403 })) as never;
        const bridge = createNetworkBridge({ fetchImpl, eventSourceFactory: undefined as never });

        const seen: Delivery[] = [];
        bridge.onDelivery(d => seen.push(d));
        bridge.postMessage({ kind: 'doc-settle' } as never);
        await bridge.flush();

        expect(bridge.delivery().queued).toBe(0);                 // dropped, not retried
        expect(bridge.delivery().rejected?.status).toBe(403);     // and reported
        expect(seen.some(d => d.rejected?.status === 403)).toBe(true);
    });

    it('a transient failure is KEPT and reported as offline', async () => {
        const { createNetworkBridge } = await import('../webview/host-bridge');
        const fetchImpl = vi.fn(async () => { throw new Error('network down'); }) as never;
        const bridge = createNetworkBridge({ fetchImpl, eventSourceFactory: undefined as never });

        bridge.postMessage({ kind: 'doc-settle' } as never);
        await bridge.flush();

        expect(bridge.delivery().state).toBe('offline');
        expect(bridge.delivery().queued).toBe(1);   // still there to retry
        expect(bridge.delivery().rejected).toBeUndefined();
    });
});
