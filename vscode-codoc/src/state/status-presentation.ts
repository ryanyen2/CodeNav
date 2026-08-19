/**
 * status-presentation.ts — PURE, vscode-free mapping of "what is codoc waiting on?"
 * to what the author is told (U7).
 *
 * Maps the codoc lifecycle + setup state to the status-bar text / tooltip /
 * click-command / warn-background flag, and (below) decides whether an accept just
 * queued code work that nothing is going to run. Kept vscode-free so `src/test/**`
 * can exercise both without the vscode host shim (per `vitest.config.mjs`);
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

import { consequenceOf, type Consequence } from './grammar';
import type { ProposalsMap } from './bindings-model';

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
        // Only reachable with `agentActive` false — the branch above outranks this
        // one — so this state IS "queued, and nobody is running it". The wording has
        // to say so: "$(play) N to implement" reads as work already in progress, and
        // authors sat watching it while nothing was ever going to drain the queue
        // (the reported bug — accepting a /codoc:plan outside the agent's blocking
        // `codoc_await_verdicts` window hands off a directive and stops there).
        // `input.detail` is deliberately NOT preferred here: the daemon writes
        // "N change(s) ready to implement — run /codoc:sync", which is the same
        // half-truth in the one place with room to correct it.
        const one = pending === 1;
        return {
            text: `$(play) codoc: ${pending} queued, not running`,
            tooltip: `${pending} accepted tree edit${one ? '' : 's'} ${one ? 'is' : 'are'} queued in `
                + `.codoc/realize.md and nothing is implementing ${one ? 'it' : 'them'}. `
                + 'Run "codoc: Implement queued changes now", or /codoc:sync in a live agent session.',
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

// ─── The stalled realize queue ───────────────────────────────────────────────
//
// Accepting a proposal that writes code mints a handed-off directive into
// `.codoc/realize.md`, stamps `awaiting_impl`, and stops. `/codoc:plan`'s primary
// path hides that, because the agent is blocked inside `codoc_await_verdicts` and
// the same turn implements the instant the Accept lands. Every other route to the
// identical click — the turn already ended, the blocking call timed out, the author
// came back to the tree ten minutes later, the tree was never opened from a session
// at all — leaves the queue with nobody assigned to it. The documented fallbacks are
// the `UserPromptSubmit` hook and `codoc watch --auto-realize`, and the extension
// refuses to enable the latter (KTD6), so the observed workaround was users typing a
// throwaway message into Claude Code purely to trip the hook.
//
// So the host asks. This lives beside the status-bar waiting text because they are
// one concern: what codoc tells an author who is waiting on work nothing is doing.

/** The label on the offer's one action — exported so the caller compares against
 *  the same string it displayed. */
export const RUN_QUEUE_ACTION = 'Run it now';

export interface RealizeOfferInput {
    /** True for Accept — a Reject hands nothing to anybody. */
    readonly accept: boolean;
    /** What accepting each proposal in the batch does to the CODE
     *  (`grammar.consequenceOf` over the sidecar's `writes_code`). */
    readonly consequences: readonly Consequence[];
    /** A coding-agent session currently owns this repo. Read on the SPAWN-grade
     *  epoch lease, not the 90 s display one: a session blocked in
     *  `codoc_await_verdicts` renews activity.json only on tool calls, so the display
     *  lease calls it dead within a minute — and offering there would start a second
     *  agent over the very turn that is waiting for this accept. */
    readonly sessionLive: boolean;
}

export interface RealizeOffer {
    readonly message: string;
    readonly action: string;
}

/** Does this verdict batch hand code work to the agent at all?
 *
 *  The trigger half of the decision, separate from {@link realizeOffer} because the
 *  host arms on the click but only offers once the daemon has actually written the
 *  queue — at click time the verdict is still an unread line in `inbox.host.jsonl`
 *  and `codoc realize` would answer "Nothing queued". */
export function queuesCodeWork(accept: boolean, consequences: readonly Consequence[]): boolean {
    return accept && consequences.some(c => c === 'build' || c === 'remove');
}

/** The offer to make, or null to stay quiet. */
export function realizeOffer(input: RealizeOfferInput): RealizeOffer | null {
    if (!queuesCodeWork(input.accept, input.consequences)) return null;
    if (input.sessionLive) return null;   // that session drains the queue itself
    const build = input.consequences.filter(c => c === 'build').length;
    const remove = input.consequences.filter(c => c === 'remove').length;
    const n = build + remove;
    // Name the destructive half explicitly. A `remove` accept deletes source, and an
    // author deciding whether to start an agent right now needs that in the sentence,
    // not in the terminal it opens.
    const what = remove === 0
        ? `${n} change${n === 1 ? '' : 's'} to build`
        : build === 0
            ? `${n} code removal${n === 1 ? '' : 's'}`
            : `${n} changes to build and to remove code`;
    return {
        message: `codoc queued ${what} — no coding-agent session is running to implement `
            + `${n === 1 ? 'it' : 'them'}.`,
        action: RUN_QUEUE_ACTION,
    };
}

/** What each event id in a verdict batch does to the code, read off the same sidecar
 *  overlay the Accept buttons were drawn from.
 *
 *  An id with no proposal row resolves to `record`: the batch is stale, or from
 *  another window's sidecar, and a proposal we cannot identify must never be the
 *  reason codoc offers to start an agent on the author's source files. */
export function verdictConsequences(
    proposals: ProposalsMap | undefined, eventIds: readonly string[],
): Consequence[] {
    const byFeature = Object.values(proposals?.by_feature ?? {});
    return eventIds.map(id => {
        const fp = byFeature.find(p => p.event_id === id);
        if (fp) return consequenceOf(fp.writes_code, fp.tag);
        const ep = proposals?.by_event?.[id];
        return ep ? consequenceOf(ep.writes_code, ep.tag) : 'record';
    });
}
