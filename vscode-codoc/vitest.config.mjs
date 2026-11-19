import { defineConfig } from 'vitest/config';

// Tests mirror `src/`: `src/test/host` for the extension host, `src/test/web`
// for the page. A module that imports `vscode` cannot be loaded here, so the
// host tests cover the files that do not. Tests run under `node`; a test that
// mounts the editor asks for `jsdom` in its first line.
export default defineConfig({
    test: {
        include: ['src/test/**/*.test.ts'],
        environment: 'node',
    },
});
