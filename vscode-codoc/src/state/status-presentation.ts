/**
 * status-presentation.ts — PURE, vscode-free status-bar mapping (U7).
 *
 * Maps the codoc lifecycle + setup state to the status-bar text / tooltip /
 * click-command / warn-background flag. Kept vscode-free so `src/test/**` can
 * exercise the precedence without the vscode host shim (per `vitest.config.ts`);
 * `workspace-state.ts` calls this and applies the result to the real
 * `StatusBarItem` (the only vscode-specific bit is turning `warn` into a
 * `ThemeColor`).
 *
 * Precedence (first match wins):
 *   1. provisioning  — setup is actively installing/indexing
 *   2. !initialized  — no `.codoc/` yet → offer one-click setup
 *   3. agent active  — a coding agent is touching files
 *   4. lifecycle     — realizing / tree_dirty / awaiting_impl / code_drift / in_sync
 */

export type CodocLifecycle =
    | 'in_sync' | 'code_drift' | 'tree_dirty' | 'awaiting_impl' | 'realizing';

export interface StatusBarInput {
    /** A `.codoc/` dir exists (the repo is initialized). */
    readonly initialized: boolean;
    /** Setup is actively provisioning (installing the core / indexing). */
    readonly provisioning: boolean;
    /** A coding agent is currently touching files. */
    readonly agentActive: boolean;
    /** Authored edits are sitting in edits.host.jsonl with nothing consuming
     *  them (status-model.daemonUnresponsive) — the daemon is down or was never
     *  started. Outranks every lifecycle state, because those are read from a
     *  status.json the dead daemon cannot be updating. */
    readonly daemonDown?: boolean;
    /** Number of files the active agent has touched (for the agent label). */
    readonly agentFileCount: number;
    /** The `.codoc/status.json` lifecycle state. */
    readonly state: CodocLifecycle;
    /** Pending proposals / queued directives, per status.json. */
    readonly pending: number;
    /** Optional detail string from status.json (used as a richer tooltip). */
    readonly detail: string;
    /** Count of non-retired features (the in_sync label). */
    readonly featureCount: number;
}

export interface StatusBarView {
    readonly text: string;
    readonly tooltip: string;
    /** Command id to run on click, or `undefined` for none (e.g. mid-setup). */
    readonly command: string | undefined;
    /** True ⇒ the caller paints the warning background (the one "you owe an action" state). */
    readonly warn: boolean;
}

export function statusBarView(input: StatusBarInput): StatusBarView {
    // 1. Provisioning — setup is running (may be a fresh repo with no .codoc/ yet).
    if (input.provisioning) {
        return {
            text: '$(cloud-download) codoc: setting up…',
            tooltip: 'Installing the codoc core and indexing your repo — see the codoc output channel.',
            command: undefined,
            warn: false,
        };
    }

    // 2. Not initialized — the one-click entry point.
    if (!input.initialized) {
        return {
            text: '$(rocket) codoc: Set up codoc',
            tooltip: 'Set up codoc to navigate your codebase as a feature tree (installs the core, no manual steps).',
            command: 'codoc.setup',
            warn: false,
        };
    }

    // 3. The daemon is not consuming edits — every lifecycle state below is
    // read from a file only the daemon updates, so showing one would repeat the
    // stale file's claim. This is the second "you owe an action" state.
    if (input.daemonDown) {
        return {
            text: '$(warning) codoc: not running',
            tooltip: 'Your tree edits are saved but nothing is applying them. '
                + 'Start the daemon in a terminal: codoc watch  (or run codoc sync once).',
            command: 'codoc.open',
            warn: true,
        };
    }

    // 4. A coding agent is working.
    if (input.agentActive) {
        return {
            text: `$(zap) codoc: agent working… (${input.agentFileCount} files)`,
            tooltip: 'A coding agent is touching files — codoc is tracking the changes.',
            command: 'codoc.open',
            warn: false,
        };
    }

    // 4. Lifecycle states (mirrors the prior status-bar semantics).
    const { state, pending } = input;
    if (state === 'realizing') {
        return {
            text: '$(loading~spin) codoc: implementing…',
            tooltip: input.detail || 'The coding agent is implementing your tree edits',
            command: 'codoc.open',
            warn: false,
        };
    }
    if (state === 'tree_dirty') {
        return {
            text: '$(pencil) codoc: applying tree edits…',
            tooltip: input.detail || 'tree.codoc was edited — realizing the code change',
            command: 'codoc.open',
            warn: false,
        };
    }
    if (state === 'awaiting_impl') {
        return {
            text: `$(play) codoc: ${pending} to implement`,
            tooltip: input.detail
                || 'Accepted tree edits are queued in .codoc/realize.md — run /codoc:sync in your Claude Code session',
            command: 'codoc.open',
            warn: true, // the one "you owe an action" state
        };
    }
    if (state === 'code_drift' || pending > 0) {
        return {
            text: `$(bell) codoc: ${pending} proposal${pending === 1 ? '' : 's'}`,
            tooltip: 'Code changed — review proposed tree updates (Accept / Reject in the editor)',
            command: 'codoc.open',
            warn: false,
        };
    }
    return {
        text: `$(check) codoc: ${input.featureCount}`,
        tooltip: `codoc: ${input.featureCount} feature${input.featureCount === 1 ? '' : 's'} — in sync`,
        command: 'codoc.open',
        warn: false,
    };
}
