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
    let signedOut = false;
    try { state = JSON.parse(localStorage.getItem('codoc:state') ?? 'null'); } catch { state = undefined; }
    w.acquireVsCodeApi = () => ({
        postMessage(m: unknown) {
            void fetch('/api/message', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'content-type': 'application/json', 'x-codoc-csrf': '1' },
                body: JSON.stringify(m),
            }).then(async r => {
                if (r.status === 401) {
                    // Navigating away here loses every word typed since the last
                    // flush, with no notice. Say so, leave the page standing, and
                    // let the person sign in again in their own time — the words
                    // are still on screen and the next flush will carry them.
                    signedOut = true;
                    window.postMessage({ kind: 'notice', text: 'your session ended; sign in again to send these edits' },
                                       window.location.origin);
                    return;
                }
                // A write that succeeds after a refused one means the viewer
                // signed in again, and the stream the 401 closed needs reopening.
                if (signedOut) { signedOut = false; window.dispatchEvent(new Event('codoc:signed-in')); }
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
    let es: EventSource | null = null;

    function listen(): void {
        es?.close();
        const stream = new EventSource('/api/events', { withCredentials: true });
        es = stream;
        for (const kind of ['projection', 'status', 'acks']) {
            stream.addEventListener(kind, ev => {
                try { window.postMessage(JSON.parse((ev as MessageEvent).data), window.location.origin); } catch { /* torn event */ }
            });
        }
        // EventSource reconnects on its own and cannot report the status it got,
        // so a session that ended mid-edit shows up here as a silent stream of
        // retries. Ask once: if the hub no longer knows this viewer, say so
        // rather than letting them keep typing into a page nothing will accept.
        stream.onerror = () => {
            if (signedOut) return;
            void fetch('/api/whoami', { credentials: 'same-origin' })
                .then(r => r.json())
                .then((me: { authenticated?: boolean }) => {
                    if (me.authenticated) return;
                    signedOut = true;
                    window.postMessage({ kind: 'notice', text: 'your session ended; sign in again to send these edits' },
                                       window.location.origin);
                })
                .catch(() => undefined);
        };
    }

    listen();
    // A 401 closes the stream for good, so when a write succeeds after a refused
    // one — the viewer signed in again — a new one is opened.
    window.addEventListener('codoc:signed-in', () => listen());
    // …and a viewer who signs in again in another tab and then only READS never
    // writes, so nothing would ask. Coming back to the tab is the moment to.
    window.addEventListener('focus', () => {
        if (!signedOut) return;
        void fetch('/api/whoami', { credentials: 'same-origin' })
            .then(r => r.json())
            .then((me: { authenticated?: boolean }) => { if (me.authenticated) { signedOut = false; listen(); } })
            .catch(() => undefined);
    });
})();
