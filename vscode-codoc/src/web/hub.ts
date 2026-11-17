/**
 * hub.ts — the page's host connection when it runs in a browser on the hub.
 *
 * The hub's HTML loads this script before the page bundle. It defines
 * `acquireVsCodeApi`, the function a VS Code webview gets from its host, so
 * web/editor/app.ts runs unchanged. `postMessage` sends a `PageToHost` message
 * to `POST /api/message` with the CSRF header the hub requires. The hub's SSE
 * stream at `/api/events` delivers `HostToPage` messages, which are re-posted
 * to the window for app.ts's one `message` listener. A reply in the POST's
 * body (`history`, or a refusal) is delivered the same way.
 *
 * It also notices a session that has ended. The page stays up with the
 * person's words on it and says to sign in again, and the stream is reopened
 * once they have.
 */
import type { HostToPage, PageToHost } from '../wire';

(() => {
    const w = window as unknown as { acquireVsCodeApi?: () => { postMessage(m: PageToHost): void } };
    if (typeof w.acquireVsCodeApi === 'function') return;   // inside VS Code: the real one exists

    const deliver = (m: HostToPage) => window.postMessage(m, window.location.origin);
    const SIGNED_OUT: HostToPage = { kind: 'notice', text: 'your session ended; sign in again to send these edits' };
    let signedOut = false;

    w.acquireVsCodeApi = () => ({
        postMessage(m) {
            void fetch('/api/message', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'content-type': 'application/json', 'x-codoc-csrf': '1' },
                body: JSON.stringify(m),
            }).then(async r => {
                if (r.status === 401) {
                    // Stay on the page. Navigating to sign-in would lose every
                    // word typed since the last flush; left standing, the next
                    // flush after signing in carries them.
                    signedOut = true;
                    deliver(SIGNED_OUT);
                    return;
                }
                // A write that succeeds after a refused one means the viewer
                // signed in again, and the stream the 401 closed needs reopening.
                if (signedOut) { signedOut = false; listen(); }
                const body = await r.json().catch(() => null) as { kind?: string; error?: string } | null;
                if (r.ok && body && typeof body.kind === 'string') { deliver(body as HostToPage); return; }
                // 202 with an `error` is the hub saying it cannot do this one
                // (it has no terminal). The person pressed a button, so tell them.
                if (body && typeof body.error === 'string') deliver({ kind: 'notice', text: body.error });
                else if (!r.ok) console.warn('codoc hub refused', m, body);
            }).catch(err => console.warn('codoc hub unreachable', err));
        },
    });

    let es: EventSource | null = null;

    function listen(): void {
        es?.close();
        const stream = new EventSource('/api/events', { withCredentials: true });
        es = stream;
        for (const kind of ['projection', 'status', 'acks']) {
            stream.addEventListener(kind, ev => {
                try { deliver(JSON.parse((ev as MessageEvent).data)); } catch { /* torn event */ }
            });
        }
        // EventSource retries on its own and cannot report the status it got,
        // so an ended session looks like a silent stream of retries. Ask once
        // whether the hub still knows this viewer.
        stream.onerror = () => {
            if (signedOut) return;
            void whoami().then(authenticated => {
                if (authenticated !== false) return;
                signedOut = true;
                deliver(SIGNED_OUT);
            });
        };
    }

    /** Whether the hub has a session for this viewer; undefined when it cannot be asked. */
    function whoami(): Promise<boolean | undefined> {
        return fetch('/api/whoami', { credentials: 'same-origin' })
            .then(r => r.json())
            .then((me: { authenticated?: boolean }) => !!me.authenticated)
            .catch(() => undefined);
    }

    listen();
    // A viewer who signs in again in another tab and then only reads never
    // writes, so no successful write reopens the stream. Coming back to the
    // tab is the moment to ask.
    window.addEventListener('focus', () => {
        if (!signedOut) return;
        void whoami().then(authenticated => { if (authenticated) { signedOut = false; listen(); } });
    });
})();
