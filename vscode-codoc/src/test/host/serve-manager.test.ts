import { describe, expect, it } from 'vitest';

import { DEFAULT_HUB_PORT, serveArgs } from '../../host/serve-manager';

describe('serveArgs', () => {
    it('defaults to the root and the default port', () => {
        expect(serveArgs({ root: '/repo' }))
            .toEqual(['serve', '--root', '/repo', '--port', String(DEFAULT_HUB_PORT)]);
    });

    it('adds --static-dir and --tunnel when given', () => {
        expect(serveArgs({ root: '/repo', port: 9000, staticDir: '/ext/dist/webview', tunnel: true }))
            .toEqual(['serve', '--root', '/repo', '--port', '9000', '--static-dir', '/ext/dist/webview', '--tunnel']);
    });

    it('leaves them out when absent or false', () => {
        const args = serveArgs({ root: '/repo', tunnel: false });
        expect(args).not.toContain('--static-dir');
        expect(args).not.toContain('--tunnel');
    });
});
