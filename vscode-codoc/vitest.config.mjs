import { defineConfig } from 'vitest/config';

export default defineConfig({
    test: {
        // Pure-logic tests only — modules under test must not import 'vscode'.
        include: ['src/test/**/*.test.ts'],
        environment: 'node',
    },
});
