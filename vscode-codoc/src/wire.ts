/**
 * wire.ts — the messages the editor page and its host exchange.
 *
 * The page (web/editor/app.ts) is one bundle that runs in two places. In VS
 * Code it is a webview and its host is the extension (host/provider.ts). On the
 * hub it is a browser tab and its host is `codoc serve`; web/hub.ts carries
 * the same messages over HTTP and SSE. Either host relays the daemon's
 * published files to the page and the page's submissions to the daemon's
 * inbox without interpreting them. The payload shapes are the daemon's own:
 * web/calc/projection.ts and web/calc/window.ts.
 *
 * Nothing here imports `vscode` or touches the DOM.
 */
import type { Ack, Projection, Submission } from './web/calc/projection';
import type { HistoryWindow } from './web/calc/window';

export type HostToPage =
    /** `projection.json`, with the features an agent is building right now. */
    | { kind: 'projection'; proj: Projection; realizing?: string[] }
    /** `status.json` plus the tree's language, and on the hub the viewer's actor and role. */
    | { kind: 'status'; status: Record<string, unknown> }
    /** The daemon's answers to this client's submissions. */
    | { kind: 'acks'; acks: Ack[] }
    /** `history.json`, sent in reply to a `history` request. */
    | { kind: 'history'; history: HistoryWindow }
    /** A sentence for the status line: something the host could not do, and why. */
    | { kind: 'notice'; text: string }
    /** Open the find widget (the VS Code keybinding reaches the page this way). */
    | { kind: 'find'; replace: boolean };

export type PageToHost =
    /** The page has loaded and wants the projection and the status. */
    | { kind: 'ready' }
    /** Ops to append to this client's inbox. */
    | { kind: 'submit'; subs: Submission[] }
    | { kind: 'history' }
    /** Run `codoc realize` where the person can watch it. */
    | { kind: 'realize' }
    /** Open the code a citation names. */
    | { kind: 'open-cite'; file: string; symbol: string }
    /** Set the language the tree is written in. */
    | { kind: 'set-language'; tag: string };
