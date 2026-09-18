import { defineConfig } from 'vitest/config';

export default defineConfig({
    test: {
        // Modules under test must not import 'vscode'. Most of these are pure
        // logic and run under `node`; `editor-dom.test.ts` mounts the real
        // editor and asks for `jsdom` in its own docblock, because the faults
        // that only appear when the browser drives it were invisible to a suite
        // that never mounted it.
        include: ['src/test/**/*.test.ts'],
        environment: 'node',
    },
});
