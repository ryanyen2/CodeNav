/**
 * hub.ts — the browser's stand-in for the VS Code webview API.
 *
 * Loaded before app.js on the hub. `acquireVsCodeApi()` returns an object whose
 * `postMessage` sends the webview's `WebviewToHost` message to `/api/message`
 * (with the CSRF header the hub requires), and the hub's SSE stream delivers
 * `HostToWebview` messages (`projection`, `status`, `acks`) which are re-posted
 * to the window so app.js's one `message` listener needs no second code path.
 */
(() => {
    const w = window as unknown as { acquireVsCodeApi?: () => unknown };
    if (typeof w.acquireVsCodeApi === 'function') return;   // inside VS Code: nothing to do
    let state: unknown;
    try { state = JSON.parse(localStorage.getItem('codoc:state') ?? 'null'); } catch { state = undefined; }
    w.acquireVsCodeApi = () => ({
        postMessage(m: unknown) {
            void fetch('/api/message', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'content-type': 'application/json', 'x-codoc-csrf': '1' },
                body: JSON.stringify(m),
            }).then(async r => {
                if (r.status === 401) { location.href = '/auth/login'; return; }
                const body = await r.json().catch(() => null) as { kind?: string } | null;
                if (r.ok && body && typeof body.kind === 'string') { window.postMessage(body, window.location.origin); return; }  // a reply the app reads as a host message
                // 202 is the hub saying it cannot do this one (it has no
                // terminal): a reply, not a failure, and the person pressed a
                // button, so they hear about it rather than nothing.
                const said = body && typeof (body as { error?: string }).error === 'string' ? (body as { error: string }).error : '';
                if (said) window.postMessage({ kind: 'notice', text: said }, window.location.origin);
                else if (!r.ok) console.warn('codoc hub refused', m, body);
            }).catch(err => console.warn('codoc hub unreachable', err));
        },
        getState: () => state,
        setState(s: unknown) { state = s; try { localStorage.setItem('codoc:state', JSON.stringify(s)); } catch { /* private mode */ } },
    });
    const es = new EventSource('/api/events', { withCredentials: true });
    for (const kind of ['projection', 'status', 'acks']) {
        es.addEventListener(kind, ev => {
            try { window.postMessage(JSON.parse((ev as MessageEvent).data), window.location.origin); } catch { /* torn event */ }
        });
    }
    es.onerror = () => { /* EventSource reconnects on its own; a 401 shows the sign-in page on reload */ };
})();
