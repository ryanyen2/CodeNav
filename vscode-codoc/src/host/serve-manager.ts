/**
 * serve-manager.ts — the arguments for `codoc serve`, the hub.
 *
 * The hub is a separate process that serves this same editor to people in a
 * browser and supervises the daemon while it runs. The extension does not own
 * it. It starts it in a terminal (extension.ts) and passes the built editor
 * bundle so the hub serves the page this extension shows.
 *
 * No `vscode` import, so vitest can load it.
 */

/** The hub's default local port (`codoc serve --port`). */
export const DEFAULT_HUB_PORT = 8787;

export interface ServeOptions {
    /** Repository root. */
    root: string;
    port?: number;
    /** The built editor bundle, `dist/webview`. Without it the hub serves a placeholder. */
    staticDir?: string;
    /** Reach the hub through a cloudflared tunnel. The hub refuses unless GitHub auth is configured. */
    tunnel?: boolean;
}

export function serveArgs(opts: ServeOptions): string[] {
    const args = ['serve', '--root', opts.root, '--port', String(opts.port ?? DEFAULT_HUB_PORT)];
    if (opts.staticDir) args.push('--static-dir', opts.staticDir);
    if (opts.tunnel) args.push('--tunnel');
    return args;
}
