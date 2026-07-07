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
                let rejected = false;
                try {
                    const res = await fetchImpl(`${base}/api/command`, {
                        method: 'POST',
                        headers: { 'content-type': 'application/json', 'x-codoc-csrf': '1' },
                        credentials: 'same-origin',
                        body: JSON.stringify(msg),
                    });
                    ok = !!res && res.ok;
                    // A definite client rejection (400-499, excluding 429 rate-limit) will
                    // never succeed on retry — e.g. a capability the caller's role doesn't
                    // have, or a malformed message. Treating it the same as a transient
                    // failure (offline / 5xx) would wedge this message at the head of the
                    // FIFO queue FOREVER, silently blocking every later postMessage (settle,
                    // comment, verdict, …) from ever syncing again. Drop it instead — the
                    // rest of the queue keeps flowing.
                    if (!ok && res && res.status >= 400 && res.status < 500 && res.status !== 429) {
                        rejected = true;
                    }
                } catch {
                    ok = false;
                }
                if (!ok && !rejected) break; // offline / 5xx / rate-limited → keep, retry later
                queue.shift();
                persist();
            }
        } finally {
            flushing = false;
        }
    }

    const esFactory = opts.eventSourceFactory ?? _defaultEventSourceFactory();
    if (esFactory) {
        const es = esFactory(`${base}${opts.eventsPath ?? '/api/events'}`);
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

function _defaultEventSourceFactory(): ((url: string) => EventSourceLike) | undefined {
    // Real EventSource in the browser; absent under node (tests inject a fake).
    if (typeof EventSource === 'undefined') return undefined;
    return (url) => new EventSource(url) as unknown as EventSourceLike;
}

/**
 * Return a VS Code-`acquireVsCodeApi()`-shaped object for the current home, so a
 * webview built for VS Code runs unchanged in a standalone browser.
 *
 * In VS Code: the real host API. In a browser: a network-backed shim that POSTs
 * commands to the hub and, on each SSE payload, RE-DISPATCHES a window `message`
 * event of the same `{kind:'doc', payload}` shape the VS Code host posts — so the
 * webview's existing message listener fires unchanged (the only edit a webview
 * needs is to call this instead of `acquireVsCodeApi()` directly).
 */
export function acquireHostApi(networkOptions?: NetworkBridgeOptions): VsCodeApi {
    if (isVsCodeHost()) return acquireVsCodeApi();

    // Default the bridge's backing store to localStorage in the browser hub so VIEW state
    // (selection / scroll / tree-width / focus-mode) and the offline outbox actually persist
    // across reloads — without this, getState() always returns undefined and restore no-ops.
    const opts: NetworkBridgeOptions = { ...networkOptions };
    if (!opts.storage && typeof localStorage !== 'undefined') opts.storage = localStorage;
    const bridge = createNetworkBridge(opts);
    bridge.onPayload((payload) => {
        if (typeof window !== 'undefined') {
            window.dispatchEvent(new MessageEvent('message', { data: { kind: 'doc', payload } }));
        }
    });
    if (typeof window !== 'undefined') {
        window.addEventListener('online', () => { void bridge.flush(); });
    }
    return {
        postMessage: (msg: unknown) => bridge.postMessage(msg as WebviewMessage),
        getState: () => bridge.getState(),
        setState: (state: unknown) => bridge.setState(state),
    };
}
