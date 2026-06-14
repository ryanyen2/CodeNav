/**
 * setup-flow.ts — PURE, vscode-free setup orchestration ORDER (vitest-testable).
 *
 * `codoc.setup` (U4) wires together the earlier units into one zero-manual-step
 * flow. The ORDER those steps run in is a correctness invariant — most sharply,
 * credentials MUST be configured BEFORE `codoc init`, because `codoc init` runs
 * the LLM bootstrap (it indexes the repo and proposes the initial tree via the
 * configured provider). If init ran before credentials, the bootstrap would have
 * no provider and fail.
 *
 * To keep that ordering testable without the vscode host, the canonical sequence
 * lives here as a plain data structure (an ordered list of step descriptors) plus
 * the small pure decision helpers (`needsSetup`, `credentialsPrecedeInit`).
 * `extension.ts` DRIVES this list; `src/test/setup-orchestration.test.ts` asserts
 * on it. No `import 'vscode'` here, ever — `vitest.config.mjs` runs `src/test/**`
 * against these and the modules under test must not pull in the vscode host shim.
 */

/** The stable id of each ordered setup step (the wire the test asserts on). */
export type SetupStepId =
    | 'ensure-uv'
    | 'provision'
    | 'credentials'
    | 'init'
    | 'start-daemon';

/** One ordered step in the setup flow — id + a human-facing progress label. */
export interface SetupStep {
    /** Stable id (drives both the test assertions and the extension's dispatch). */
    readonly id: SetupStepId;
    /** Short progress label surfaced in the OutputChannel / progress notification. */
    readonly label: string;
}

/**
 * The canonical setup order. `extension.ts` walks this list in sequence:
 *   1. ensure-uv     — `ensureUv()`: probe/bootstrap the uv package manager.
 *   2. provision     — `provisionCodoc()`: install the codoc core into an isolated
 *                      uv tool env and resolve the `codoc` / `codoc-mcp` paths.
 *   3. credentials   — `bootstrapCredentials()`: pick a reflection provider and
 *                      write `.env` — MUST precede init so init's LLM bootstrap
 *                      has a provider (the correctness invariant).
 *   4. init          — run `codoc init` (indexes the repo + LLM-proposes the tree;
 *                      wires hooks/MCP/skill/commands; writes status.json).
 *   5. start-daemon  — `startDaemon()`: spawn the managed `codoc watch` daemon.
 */
export const SETUP_STEPS: readonly SetupStep[] = [
    { id: 'ensure-uv', label: 'Checking for uv…' },
    { id: 'provision', label: 'Installing the codoc core…' },
    { id: 'credentials', label: 'Configuring the reflection provider…' },
    { id: 'init', label: 'Indexing the repo and proposing the feature tree…' },
    { id: 'start-daemon', label: 'Starting the codoc daemon…' },
] as const;

/** The ordered step ids — a convenience projection of {@link SETUP_STEPS}. */
export function setupStepIds(): SetupStepId[] {
    return SETUP_STEPS.map(s => s.id);
}

/**
 * The correctness invariant, expressed as a pure predicate over the step order:
 * credentials must be configured BEFORE `codoc init` runs its LLM bootstrap.
 *
 * @param steps the ordered step list (defaults to {@link SETUP_STEPS}).
 * @returns `true` iff the `credentials` step precedes the `init` step.
 */
export function credentialsPrecedeInit(steps: readonly SetupStep[] = SETUP_STEPS): boolean {
    const credIdx = steps.findIndex(s => s.id === 'credentials');
    const initIdx = steps.findIndex(s => s.id === 'init');
    if (credIdx < 0 || initIdx < 0) return false;
    return credIdx < initIdx;
}

/**
 * Whether the first-run "Set up codoc" entry point should be offered, given what
 * activation observed. Setup is needed when the repo is not yet initialized (no
 * `.codoc/`) AND nothing has been provisioned (no cached executables) — i.e. a
 * fresh repo with only the extension installed. Once either exists, the normal
 * status-driven UI takes over and we don't re-offer setup.
 *
 * @param hasCodocDir whether a `.codoc/` directory exists in the workspace.
 * @param hasCachedExecs whether the resolved codoc executables are cached.
 */
export function needsSetup(hasCodocDir: boolean, hasCachedExecs: boolean): boolean {
    return !hasCodocDir && !hasCachedExecs;
}
