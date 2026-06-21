/**
 * serve-manager.ts — the pure logic behind the extension's "deployed hub" commands.
 *
 * `codoc serve` (codoc/cli/main.py) is the deployed-page + multi-user hub: a
 * separate process, peer to the extension, that supervises the daemon and serves
 * the same intent-tree editor as a web app (localhost by default; remote over a
 * tunnel + GitHub App). The extension doesn't *own* the hub (it owns the local
 * `codoc watch` daemon and defers when the hub owns it) — it just makes the hub
 * easy to START and SHARE. This module is the host-free, unit-tested core of that:
 * the `codoc serve …` argv, the localhost URL, and the terminal command line.
 *
 * Kept vscode-free so it runs under vitest (the "no 'vscode' import" rule); the
 * terminal/notification/status-bar glue lives in extension.ts.
 */

/** Default local port for the hub (mirrors `codoc serve --port`, cli/main.py). */
export const DEFAULT_HUB_PORT = 8787;

export interface ServeOptions {
    /** Repository root (`--root`). */
    root: string;
    /** Local port (`--port`); defaults to {@link DEFAULT_HUB_PORT}. */
    port?: number;
    /** Built standalone SPA dir (`--static-dir`) — the extension's `dist/webview`,
     *  so the hub serves the real editor instead of the placeholder page. */
    staticDir?: string;
    /** Expose over a cloudflared tunnel (`--tunnel`) for remote contributors —
     *  needs cloudflared + a Cloudflare Access / GitHub App (deploy config). */
    tunnel?: boolean;
}

/** The argv passed to the `codoc` binary for `codoc serve …`. Pure. */
export function serveArgs(opts: ServeOptions): string[] {
    const args = ['serve', '--root', opts.root,
        '--port', String(opts.port ?? DEFAULT_HUB_PORT)];
    if (opts.staticDir) args.push('--static-dir', opts.staticDir);
    if (opts.tunnel) args.push('--tunnel');
    return args;
}

/** The localhost URL the hub serves on (the link to open / share on the LAN —
 *  remote reach is via the tunnel, never by binding 0.0.0.0). Pure. */
export function hubUrl(port: number = DEFAULT_HUB_PORT): string {
    return `http://127.0.0.1:${port}`;
}

/** Quote a path for a POSIX shell only when it needs it (spaces / specials), so
 *  the common no-space path stays clean in the terminal echo. */
export function shellQuote(arg: string): string {
    return /[^\w@%+=:,./-]/.test(arg) ? `'${arg.replace(/'/g, `'\\''`)}'` : arg;
}

/** The full command line to send to an integrated terminal. `codoc` defaults to
 *  the bare binary (resolved on the shell's PATH, as `codoc.sync` does). Pure. */
export function serveCommandLine(opts: ServeOptions, codoc = 'codoc'): string {
    return [shellQuote(codoc), ...serveArgs(opts).map(shellQuote)].join(' ');
}
