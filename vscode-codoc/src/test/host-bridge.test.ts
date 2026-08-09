/**
 * host-bridge.test.ts — the transport seam (U2).
 *
 * Imports only from `../webview/host-bridge` (+ protocol types), never `vscode`,
 * so it runs under vitest. The VS Code path uses `window`/`acquireVsCodeApi` and
 * is exercised only via selection; the NETWORK bridge is fully injected (fetch,
 * EventSource, storage) and unit-tested here — the monotonic payload guard and
 * the offline outbox are the new, load-bearing behaviors.
 */
import { describe, it, expect, vi } from 'vitest';
import {
    acquireHostApi,
    createNetworkBridge,
    isVsCodeHost,
    type EventSourceLike,
    type StorageLike,
} from '../webview/host-bridge';
import type { DocPayload, WebviewMessage } from '../webview/protocol';

class FakeStorage implements StorageLike {
    map = new Map<string, string>();
    getItem(k: string): string | null {
        return this.map.has(k) ? (this.map.get(k) as string) : null;
    }
    setItem(k: string, v: string): void {
        this.map.set(k, v);
    }
}

class FakeEventSource implements EventSourceLike {
    listeners: ((ev: { data: string }) => void)[] = [];
    closed = false;
    addEventListener(_t: 'message', l: (ev: { data: string }) => void): void {
        this.listeners.push(l);
    }
    close(): void {
        this.closed = true;
    }
    emit(data: string): void {
        for (const l of this.listeners) l({ data });
    }
}

const payload = (rev: number): DocPayload => ({
    nodes: {},
    roots: [],
    status: { state: 'in_sync', pending: 0 },
    sync: { state: 'in_sync', pending: 0, activeWrite: [], activeRead: [], phase: {} },
    rootName: 'r',
    pendingEventIds: [],
    rev,
});

describe('network bridge — SSE inbound', () => {
    it('dispatches payloads and drops stale (lower-rev) ones', () => {
        const es = new FakeEventSource();
        const got: number[] = [];
        const bridge = createNetworkBridge({ eventSourceFactory: () => es });
        bridge.onPayload((p) => got.push(p.rev));
        es.emit(JSON.stringify(payload(1)));
        es.emit(JSON.stringify(payload(3)));
        es.emit(JSON.stringify(payload(2))); // stale → dropped
        es.emit(JSON.stringify(payload(4)));
        expect(got).toEqual([1, 3, 4]);
    });

    it('ignores malformed SSE data', () => {
        const es = new FakeEventSource();
        const got: number[] = [];
        const bridge = createNetworkBridge({ eventSourceFactory: () => es });
        bridge.onPayload((p) => got.push(p.rev));
        es.emit('not-json{');
        es.emit(JSON.stringify(payload(1)));
        expect(got).toEqual([1]);
    });

    it('unsubscribe stops delivery', () => {
        const es = new FakeEventSource();
        const got: number[] = [];
        const bridge = createNetworkBridge({ eventSourceFactory: () => es });
        const off = bridge.onPayload((p) => got.push(p.rev));
        es.emit(JSON.stringify(payload(1)));
        off();
        es.emit(JSON.stringify(payload(2)));
        expect(got).toEqual([1]);
    });
});

describe('network bridge — outbound commands + offline outbox (R14)', () => {
    it('POSTs commands to /api/command with the message body', async () => {
        const calls: { url: string; init: RequestInit }[] = [];
        const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
            calls.push({ url, init });
            return { ok: true } as Response;
        });
        const bridge = createNetworkBridge({
            fetchImpl: fetchImpl as unknown as typeof fetch,
            storage: new FakeStorage(),
        });
        const msg: WebviewMessage = { kind: 'hand-off' };
        bridge.postMessage(msg);
        await vi.waitFor(() => expect(calls.length).toBe(1));
        expect(calls[0].url).toBe('/api/command');
        expect(calls[0].init.method).toBe('POST');
        expect(JSON.parse(calls[0].init.body as string)).toEqual(msg);
    });

    it('keeps a command queued while offline and drains it on reconnect', async () => {
        const storage = new FakeStorage();
        let online = false;
        const fetchImpl = vi.fn(async () => {
            if (!online) throw new Error('offline');
            return { ok: true } as Response;
        });
        const bridge = createNetworkBridge({
            fetchImpl: fetchImpl as unknown as typeof fetch,
            storage,
        });
        bridge.postMessage({ kind: 'verdict', eventIds: ['e1'], accept: true });
        await bridge.flush();
        // offline: still queued + persisted, nothing lost
        expect(JSON.parse(storage.getItem('codoc.outbox') as string)).toHaveLength(1);

        online = true;
        await bridge.flush();
        expect(JSON.parse(storage.getItem('codoc.outbox') as string)).toHaveLength(0);
    });

    it('retries a transient failure on its own clock, with no further user action', async () => {
        // P-5: the outbox used to drain only on an `online` event or the next
        // postMessage. A hub restart or a momentary 502 fires no `online` — the network
        // never went down — and an author who has just finished editing sends nothing
        // more. Their last settle sat in the queue looking sent until they typed again.
        vi.useFakeTimers();
        try {
            let up = false;
            const fetchImpl = vi.fn(async () => (up ? { ok: true } : { ok: false, status: 502 }) as Response);
            const bridge = createNetworkBridge({
                fetchImpl: fetchImpl as unknown as typeof fetch,
                storage: new FakeStorage(),
            });
            bridge.postMessage({ kind: 'hand-off' });
            await vi.waitFor(() => expect(fetchImpl.mock.calls.length).toBe(1));
            expect(bridge.delivery().state).toBe('offline');

            // Still down: the retry fires anyway, and backs off rather than spinning.
            await vi.advanceTimersByTimeAsync(1_000);
            expect(fetchImpl.mock.calls.length).toBe(2);
            await vi.advanceTimersByTimeAsync(1_000);
            expect(fetchImpl.mock.calls.length).toBe(2);   // next attempt is at 2s
            await vi.advanceTimersByTimeAsync(1_000);
            expect(fetchImpl.mock.calls.length).toBe(3);

            up = true;
            await vi.advanceTimersByTimeAsync(4_000);
            expect(bridge.delivery()).toEqual({ state: 'live', queued: 0 });

            // Drained: no timer is left running to poll a hub that is answering.
            const settled = fetchImpl.mock.calls.length;
            await vi.advanceTimersByTimeAsync(60_000);
            expect(fetchImpl.mock.calls.length).toBe(settled);
            bridge.dispose();
        } finally {
            vi.useRealTimers();
        }
    });

    it('does not retry a definite rejection — it drops it and keeps the queue moving', async () => {
        // A capability the caller lacks never succeeds on retry; retrying it forever
        // would wedge every later message behind it AND hammer the hub.
        vi.useFakeTimers();
        try {
            const fetchImpl = vi.fn(async () => ({ ok: false, status: 403 }) as Response);
            const bridge = createNetworkBridge({
                fetchImpl: fetchImpl as unknown as typeof fetch,
                storage: new FakeStorage(),
            });
            bridge.postMessage({ kind: 'hand-off' });
            await vi.waitFor(() => expect(fetchImpl.mock.calls.length).toBe(1));

            await vi.advanceTimersByTimeAsync(60_000);
            expect(fetchImpl.mock.calls.length).toBe(1);
            expect(bridge.delivery().queued).toBe(0);
            expect(bridge.delivery().rejected?.status).toBe(403);
            bridge.dispose();
        } finally {
            vi.useRealTimers();
        }
    });

    it('dispose stops the retry timer and closes the stream', async () => {
        vi.useFakeTimers();
        try {
            const es = new FakeEventSource();
            const fetchImpl = vi.fn(async () => { throw new Error('offline'); });
            const bridge = createNetworkBridge({
                fetchImpl: fetchImpl as unknown as typeof fetch,
                eventSourceFactory: () => es,
                storage: new FakeStorage(),
            });
            bridge.postMessage({ kind: 'hand-off' });
            await vi.waitFor(() => expect(fetchImpl.mock.calls.length).toBe(1));

            bridge.dispose();
            await vi.advanceTimersByTimeAsync(60_000);
            expect(fetchImpl.mock.calls.length).toBe(1);
            expect(es.closed).toBe(true);
        } finally {
            vi.useRealTimers();
        }
    });

    it('restores a persisted outbox on construction and drains it on flush', async () => {
        const storage = new FakeStorage();
        storage.setItem('codoc.outbox', JSON.stringify([{ kind: 'hand-off' }]));
        const fetchImpl = vi.fn(async () => ({ ok: true }) as Response);
        const bridge = createNetworkBridge({
            fetchImpl: fetchImpl as unknown as typeof fetch,
            storage,
        });
        await bridge.flush();
        expect(fetchImpl).toHaveBeenCalledTimes(1);
        expect(JSON.parse(storage.getItem('codoc.outbox') as string)).toHaveLength(0);
    });

    it('does not drop the queue when the hub returns a non-OK status', async () => {
        const storage = new FakeStorage();
        const fetchImpl = vi.fn(async () => ({ ok: false, status: 503 }) as Response);
        const bridge = createNetworkBridge({
            fetchImpl: fetchImpl as unknown as typeof fetch,
            storage,
        });
        bridge.postMessage({ kind: 'hand-off' });
        await bridge.flush();
        expect(JSON.parse(storage.getItem('codoc.outbox') as string)).toHaveLength(1);
    });

    it('keeps retrying on a 429 rate-limit (transient, not a permanent rejection)', async () => {
        const storage = new FakeStorage();
        const fetchImpl = vi.fn(async () => ({ ok: false, status: 429 }) as Response);
        const bridge = createNetworkBridge({
            fetchImpl: fetchImpl as unknown as typeof fetch,
            storage,
        });
        bridge.postMessage({ kind: 'hand-off' });
        await bridge.flush();
        expect(JSON.parse(storage.getItem('codoc.outbox') as string)).toHaveLength(1);
    });

    it('drops a permanently-rejected (4xx) command instead of wedging the queue forever', async () => {
        const storage = new FakeStorage();
        const fetchImpl = vi.fn(async () => ({ ok: false, status: 403 }) as Response);
        const bridge = createNetworkBridge({
            fetchImpl: fetchImpl as unknown as typeof fetch,
            storage,
        });
        bridge.postMessage({ kind: 'block-edit', block: { block_id: 'blk-1', feature_id: 'f-1', kind: 'url', action: 'add', content: 'x', prev_content: '' } });
        await bridge.flush();
        expect(JSON.parse(storage.getItem('codoc.outbox') as string)).toHaveLength(0);
    });

    it('a dropped 4xx message does not block a later message behind it', async () => {
        const storage = new FakeStorage();
        const calls: string[] = [];
        const fetchImpl = vi.fn(async (_url: string, init: RequestInit) => {
            const body = JSON.parse(init.body as string) as WebviewMessage;
            calls.push(body.kind);
            return body.kind === 'hand-off' ? ({ ok: false, status: 403 }) as Response : ({ ok: true }) as Response;
        });
        const bridge = createNetworkBridge({
            fetchImpl: fetchImpl as unknown as typeof fetch,
            storage,
        });
        bridge.postMessage({ kind: 'hand-off' }); // will be rejected + dropped
        bridge.postMessage({ kind: 'withdraw-realization', featureId: 'f-1' }); // must still get through
        await vi.waitFor(() => expect(calls).toEqual(['hand-off', 'withdraw-realization']));
        await vi.waitFor(() =>
            expect(JSON.parse(storage.getItem('codoc.outbox') as string)).toHaveLength(0));
    });
});

describe('view state + transport selection', () => {
    it('persists view state (not tokens) via storage', () => {
        const storage = new FakeStorage();
        const bridge = createNetworkBridge({ storage });
        expect(bridge.getState()).toBeUndefined();
        bridge.setState({ glance: true });
        expect(bridge.getState()).toEqual({ glance: true });
    });

    it('acquireHostApi returns a usable VsCodeApi shim outside VS Code', () => {
        expect(isVsCodeHost()).toBe(false);
        const api = acquireHostApi();
        expect(typeof api.postMessage).toBe('function');
        expect(typeof api.getState).toBe('function');
        expect(typeof api.setState).toBe('function');
    });

    it('acquireHostApi defaults its store to the ambient localStorage (browser-hub view state persists)', () => {
        // The browser-hub regression: without a default storage, getState() always returned
        // undefined and UiState restore (selection / scroll / tree-width / focus-mode) no-oped.
        const fake = new FakeStorage();
        (globalThis as unknown as { localStorage?: StorageLike }).localStorage = fake;
        try {
            const api = acquireHostApi();
            api.setState({ focusMode: true, treeWidth: 320 });
            expect(api.getState()).toEqual({ focusMode: true, treeWidth: 320 });
            expect(fake.getItem('codoc.viewstate')).toContain('focusMode'); // persisted, not in-memory
        } finally {
            delete (globalThis as unknown as { localStorage?: StorageLike }).localStorage;
        }
    });
});
