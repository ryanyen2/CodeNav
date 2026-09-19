// Review-8 probe config. From <worktree>/vscode-codoc:
//   npx vitest run --config <scratchpad>/vitest.probe8.config.mjs
// (add `-t 'probe8 editor'` / `'probe8 app'` / `'probe8 hub shell'` to run one file;
// the cost sections print their table and write probe8_cost.txt beside this file.)
// No `defineConfig` import: this file lives outside the extension's node_modules.
const S = '/private/tmp/claude-501/-Users-r4yen-Desktop-Research-CoDoc-repos-CodeNav/d28f4a40-c80f-4f4e-b0c9-17b9d836ed30/scratchpad';

export default {
    test: {
        include: [`${S}/probe8_*.test.ts`],
        environment: 'jsdom',
        testTimeout: 180_000,
        hookTimeout: 180_000,
        // app.ts and hub.ts keep module-level state; one file per shell, in sequence
        // so the cost numbers are not taken under another file's load.
        fileParallelism: false,
    },
};
