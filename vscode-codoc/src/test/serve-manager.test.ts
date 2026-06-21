/**
 * serve-manager.test.ts — the pure logic behind the "deployed hub" commands.
 *
 * The terminal/notification glue lives in extension.ts (host-coupled); these pin
 * the argv, the localhost URL, and the shell command line the extension sends to
 * launch `codoc serve` (the page-deployment + multi-user hub).
 */
import { describe, it, expect } from 'vitest';
import {
    DEFAULT_HUB_PORT, serveArgs, hubUrl, serveCommandLine, shellQuote,
} from '../serve/serve-manager';

describe('serveArgs — the `codoc serve …` argv', () => {
    it('defaults to root + the default port', () => {
        expect(serveArgs({ root: '/repo' }))
            .toEqual(['serve', '--root', '/repo', '--port', String(DEFAULT_HUB_PORT)]);
    });

    it('includes --static-dir and --tunnel when given', () => {
        expect(serveArgs({ root: '/repo', port: 9000, staticDir: '/ext/dist/webview', tunnel: true }))
            .toEqual([
                'serve', '--root', '/repo', '--port', '9000',
                '--static-dir', '/ext/dist/webview', '--tunnel',
            ]);
    });

    it('omits --static-dir / --tunnel when absent or false', () => {
        const args = serveArgs({ root: '/repo', tunnel: false });
        expect(args).not.toContain('--static-dir');
        expect(args).not.toContain('--tunnel');
    });
});

describe('hubUrl — the localhost link to open / share', () => {
    it('binds 127.0.0.1 on the given (or default) port', () => {
        expect(hubUrl()).toBe(`http://127.0.0.1:${DEFAULT_HUB_PORT}`);
        expect(hubUrl(9000)).toBe('http://127.0.0.1:9000');
    });
});

describe('shellQuote / serveCommandLine — the terminal command', () => {
    it('leaves a plain path unquoted, quotes one with spaces', () => {
        expect(shellQuote('/usr/bin/codoc')).toBe('/usr/bin/codoc');
        expect(shellQuote('/My Repo/x')).toBe(`'/My Repo/x'`);
    });

    it('builds a runnable command line, quoting a spaced root', () => {
        expect(serveCommandLine({ root: '/repo' }))
            .toBe(`codoc serve --root /repo --port ${DEFAULT_HUB_PORT}`);
        expect(serveCommandLine({ root: '/My Repo', staticDir: '/ext/dist/webview' }, '/v/codoc'))
            .toBe(`/v/codoc serve --root '/My Repo' --port ${DEFAULT_HUB_PORT} --static-dir /ext/dist/webview`);
    });
});
