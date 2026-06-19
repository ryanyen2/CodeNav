/**
 * host-bridge.ts — the transport seam between the webview client and its host (U2).
 *
 * The editor bundle runs in two homes:
 *   • a VS Code webview — talks to the extension host via `acquireVsCodeApi()`
 *     + `window` message events;
 *   • a standalone browser served by `codoc serve` — talks to the hub over
 *     HTTP POST (client→host commands) + SSE (host→client `DocPayload`s).
 *
 * Both speak the same `protocol.ts` contract. This module hides which transport
 * is live behind one {@link HostBridge} interface so the editor calls a single
 * surface. The VS Code path is unchanged in spirit; the network path adds the
 * two things a remote surface needs: an OFFLINE OUTBOX (a failed command is
 * queued and retried on reconnect — plan R14) and a MONOTONIC payload guard (a
 * lower-version `DocPayload` is dropped, so a reconnect/restart can't regress
 * the view — plan KTD8 / F10).
 *
 * Pure + injectable: every ambient dependency (fetch, EventSource, storage) is
 * a parameter, so the network bridge is unit-testable under node with no DOM.
 */
import type { DocPayload, WebviewMessage } from './protocol';

/** The one surface the editor talks to, regardless of transport. */
export interface HostBridge {
    /** Send a client→host message (fire-and-forget; the network impl queues it). */
    postMessage(msg: WebviewMessage): void;
    /** Subscribe to host→client `DocPayload`s. Returns an unsubscribe fn. */
    onPayload(cb: (payload: DocPayload) => void): () => void;
    /** Persisted VIEW state (glance toggle, scroll) — never auth tokens. */
    getState<T = unknown>(): T | undefined;
    setState<T = unknown>(state: T): void;
}

/** True when running inside a VS Code webview (the host API global is present). */
export function isVsCodeHost(): boolean {
    return typeof (globalThis as Record<string, unknown>).acquireVsCodeApi === 'function';
}

// ---------------------------------------------------------------------------
// VS Code webview transport
// ---------------------------------------------------------------------------

interface VsCodeApi {
    postMessage(msg: unknown): void;
    getState(): unknown;
    setState(state: unknown): void;
}
declare function acquireVsCodeApi(): VsCodeApi;

/** Bridge over the VS Code webview message bus. Browser-only (uses `window`). */
export function createVsCodeBridge(): HostBridge {
    const api = acquireVsCodeApi();
    return {
        postMessage: (msg) => api.postMessage(msg),
        onPayload: (cb) => {
            const handler = (e: MessageEvent) => {
                const data = e.data as { kind?: string; payload?: DocPayload } | undefined;
                if (data && data.kind === 'doc' && data.payload) cb(data.payload);
            };
            window.addEventListener('message', handler);
            return () => window.removeEventListener('message', handler);
        },
        getState: <T,>() => api.getState() as T | undefined,
        setState: (s) => api.setState(s),
    };
}

// ---------------------------------------------------------------------------
// Standalone-browser (codoc serve) transport
// ---------------------------------------------------------------------------

/** Minimal SSE surface we depend on — real `EventSource` satisfies it. */
export interface EventSourceLike {
    addEventListener(type: 'message', listener: (ev: { data: string }) => void): void;
    close(): void;
}

/** Minimal `Storage` surface (localStorage satisfies it). */
export interface StorageLike {
    getItem(key: string): string | null;
    setItem(key: string, value: string): void;
}

export interface NetworkBridgeOptions {
    /** Origin/base prefix for endpoints (default ''). Commands POST to
     *  `${base}/api/command`; SSE connects to `${base}${eventsPath}`. */
    base?: string;
    eventsPath?: string;
    fetchImpl?: typeof fetch;
    eventSourceFactory?: (url: string) => EventSourceLike;
    /** Backing store for the offline outbox + view state (localStorage in the browser). */
    storage?: StorageLike;
}

/** A network bridge exposes `flush()` so the shell can retry the outbox on an
 *  `online` event / interval without the bridge importing `window`. */
export interface NetworkBridge extends HostBridge {
    flush(): Promise<void>;
}

const OUTBOX_KEY = 'codoc.outbox';
const VIEWSTATE_KEY = 'codoc.viewstate';

/**
 * Bridge over the `codoc serve` hub: HTTP POST out, SSE in.
 *
 * Outbox: every `postMessage` is appended to a persisted queue and a flush is
 * kicked. A failed POST (offline / hub down) leaves the queue intact to retry —
 * so a suggestion made during an outage is never lost (R14). SSE payloads below
 * the last applied `rev` are dropped (monotonic guard).
 */
export function createNetworkBridge(opts: NetworkBridgeOptions = {}): NetworkBridge {
    const base = opts.base ?? '';
    const fetchImpl = opts.fetchImpl ?? (globalThis.fetch as typeof fetch);
    const storage = opts.storage;
    const listeners = new Set<(p: DocPayload) => void>();
    let lastRev = Number.NEGATIVE_INFINITY;

    let queue: WebviewMessage[] = [];
    if (storage) {
        try {
            const raw = storage.getItem(OUTBOX_KEY);
            if (raw) queue = JSON.parse(raw) as WebviewMessage[];
        } catch {
            queue = [];
        }
    }
    const persist = () => storage?.setItem(OUTBOX_KEY, JSON.stringify(queue));

    let flushing = false;
    async function flush(): Promise<void> {
        if (flushing) return;
        flushing = true;
        try {
            while (queue.length) {
                const msg = queue[0];
                let ok = false;
                try {
                    const res = await fetchImpl(`${base}/api/command`, {
                        method: 'POST',
                        headers: { 'content-type': 'application/json', 'x-codoc-csrf': '1' },
                        credentials: 'same-origin',
                        body: JSON.stringify(msg),
                    });
                    ok = !!res && res.ok;
                } catch {
                    ok = false;
                }
                if (!ok) break; // offline / server error → keep the queue, retry later
                queue.shift();
                persist();
            }
        } finally {
            flushing = false;
        }
    }

    if (opts.eventSourceFactory) {
        const es = opts.eventSourceFactory(`${base}${opts.eventsPath ?? '/api/events'}`);
        es.addEventListener('message', (ev) => {
            let payload: DocPayload;
            try {
                payload = JSON.parse(ev.data) as DocPayload;
            } catch {
                return;
            }
            if (typeof payload.rev !== 'number' || payload.rev < lastRev) return;
            lastRev = payload.rev;
            for (const cb of listeners) cb(payload);
        });
    }

    return {
        postMessage(msg) {
            queue.push(msg);
            persist();
            void flush();
        },
        onPayload(cb) {
            listeners.add(cb);
            return () => listeners.delete(cb);
        },
        getState<T,>() {
            if (!storage) return undefined;
            try {
                const raw = storage.getItem(VIEWSTATE_KEY);
                return raw ? (JSON.parse(raw) as T) : undefined;
            } catch {
                return undefined;
            }
        },
        setState(state) {
            storage?.setItem(VIEWSTATE_KEY, JSON.stringify(state));
        },
        flush,
    };
}

/** Select the transport for the current home. The standalone shell passes
 *  `networkOptions`; inside VS Code the host API is present and wins. */
export function createHostBridge(networkOptions?: NetworkBridgeOptions): HostBridge {
    return isVsCodeHost() ? createVsCodeBridge() : createNetworkBridge(networkOptions);
}
