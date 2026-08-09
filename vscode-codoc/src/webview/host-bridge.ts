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

/**
 * What is happening to the things this client sends — the one fact the browser
 * hub never told anyone.
 *
 * `state` answers "will what I type next actually land": `live` when the queue is
 * empty and the last POST succeeded, `queued` while messages are in flight,
 * `offline` once a POST has failed and the queue is only growing.
 *
 * It is derived from the POST path, NOT from the SSE stream. SSE governs whether
 * updates arrive, and EventSource reconnects on its own — wiring an indicator to
 * it would flicker "offline" through every routine reconnect while saying nothing
 * about whether the user's edit is safe. The POST path is the one that answers
 * the question actually being asked.
 *
 * `rejected` carries the last message the hub REFUSED (4xx). Those are dropped
 * from the queue on purpose — a capability the caller lacks never succeeds on
 * retry, and keeping it would wedge every later message behind it — but dropping
 * silently is how a read collaborator's prose used to disappear: refused by the
 * hub, removed from the outbox, still on screen looking saved until the next
 * projection replaced it. Reporting it is what makes the drop honest.
 */
export interface Delivery {
    state: 'live' | 'queued' | 'offline';
    queued: number;
    rejected?: { msg: WebviewMessage; status: number };
}

/** A network bridge exposes `flush()` so the shell can retry the outbox on an
 *  `online` event / interval without the bridge importing `window`. */
export interface NetworkBridge extends HostBridge {
    flush(): Promise<void>;
    /** Subscribe to delivery changes. Returns an unsubscribe fn. */
    onDelivery(cb: (d: Delivery) => void): () => void;
    /** The current delivery state, for a late subscriber. */
    delivery(): Delivery;
    /** Cancel the retry timer and close the SSE stream. For tests and teardown; a
     *  browser page normally lives as long as the bridge. */
    dispose(): void;
}

/** Backoff for retrying a TRANSIENT failure (offline, 5xx, 429).
 *
 *  Without a timer the outbox only drained on an `online` event or the next
 *  `postMessage`, and neither is guaranteed to come: a hub restart or a momentary 502
 *  fires no `online` (the network never went down), and an author who has just finished
 *  editing sends nothing more. Their last settle then sat in the queue looking sent,
 *  until they happened to type again — the delivery indicator said `offline`, which is
 *  honest, but nothing was working to change it.
 *
 *  Doubling from a second to half a minute keeps a brief blip nearly invisible while a
 *  hub that is down for an hour is polled twice a minute rather than continuously. */
const RETRY_BASE_MS = 1_000;
const RETRY_MAX_MS = 30_000;

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

    const deliveryListeners = new Set<(d: Delivery) => void>();
    let lastPostFailed = false;
    let lastRejected: { msg: WebviewMessage; status: number } | undefined;
    const delivery = (): Delivery => ({
        state: queue.length === 0 ? 'live' : lastPostFailed ? 'offline' : 'queued',
        queued: queue.length,
        ...(lastRejected ? { rejected: lastRejected } : {}),
    });
    const announce = () => {
        const d = delivery();
        // Snapshot the listener set: a subscriber that unsubscribes while being
        // notified would otherwise mutate the set mid-iteration.
        for (const cb of [...deliveryListeners]) cb(d);
    };

    // One retry timer for the whole queue, not one per message: the queue is a FIFO
    // drained by a single flush, so a timer per message would stampede the hub with N
    // concurrent flushes the moment it came back.
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let retryDelay = RETRY_BASE_MS;
    function scheduleRetry(): void {
        if (retryTimer !== null) return;
        retryTimer = setTimeout(() => { retryTimer = null; void flush(); }, retryDelay);
        retryDelay = Math.min(retryDelay * 2, RETRY_MAX_MS);
    }
    function cancelRetry(): void {
        if (retryTimer !== null) clearTimeout(retryTimer);
        retryTimer = null;
        retryDelay = RETRY_BASE_MS;   // the next outage starts from a fast retry again
    }

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
                        lastRejected = { msg, status: res.status };
                    }
                } catch {
                    ok = false;
                }
                if (!ok && !rejected) {
                    // offline / 5xx / rate-limited → keep, retry later
                    lastPostFailed = true;
                    break;
                }
                lastPostFailed = false;
                queue.shift();
                persist();
            }
        } finally {
            flushing = false;
            // Anything still queued failed transiently (a definite rejection is dropped
            // above), so keep trying on our own clock; an empty queue re-arms the backoff.
            if (queue.length) scheduleRetry(); else cancelRetry();
            announce();
        }
    }

    const esFactory = opts.eventSourceFactory ?? _defaultEventSourceFactory();
    let source: EventSourceLike | null = null;
    if (esFactory) {
        const es = esFactory(`${base}${opts.eventsPath ?? '/api/events'}`);
        source = es;
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
            announce();          // queued, before the POST is even attempted
            void flush();
        },
        onPayload(cb) {
            listeners.add(cb);
            return () => listeners.delete(cb);
        },
        onDelivery(cb) {
            deliveryListeners.add(cb);
            return () => deliveryListeners.delete(cb);
        },
        delivery,
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
        dispose() {
            cancelRetry();
            source?.close();
            source = null;
        },
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
    // Delivery arrives the same way payloads do, so the shell keeps ONE inbound
    // path (`window` messages) rather than growing a second, transport-specific
    // subscription that only exists in the browser.
    bridge.onDelivery((delivery) => {
        if (typeof window !== 'undefined') {
            window.dispatchEvent(new MessageEvent('message', { data: { kind: 'delivery', delivery } }));
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
