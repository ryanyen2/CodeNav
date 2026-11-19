import { defineConfig } from 'vitest/config';

// Tests mirror `src/`: `src/test/host` for the extension host, `src/test/web`
// for the page. There is no `vscode` module outside VS Code, so a host test of
// a module that imports it supplies one with `vi.mock('vscode', ...)`. Tests run
// under `node`; a test that mounts the editor asks for `jsdom` in its first line.
export default defineConfig({
    test: {
        include: ['src/test/**/*.test.ts'],
        environment: 'node',
    },
});
