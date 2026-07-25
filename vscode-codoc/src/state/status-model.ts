/**
 * status-model.ts — PURE, vscode-free lease decay for .codoc/status.json.
 *
 * The IDE reads status.json directly and never calls the Python `refresh_status`
 * (it is a passive file watcher, not the daemon). So a `realizing` state written
 * by a `/codoc:sync` that was then killed/interrupted would otherwise keep the
 * status bar spinning "implementing…" forever — the daemon only decays the state
 * when *it* next runs, which may be never if nothing else touches the repo
 * (review #9). This mirrors Python `status.leased_display_state`: a `realizing`
 * write older than `REALIZING_LEASE_MS` is a dead pass and decays to the same
 * ground truth the daemon would eventually persist (awaiting_impl if a queue
 * remains, else in_sync).
 *
 * No I/O here — workspace-state.ts reads the file + its mtime and calls this,
 * keeping it unit-testable without a VS Code host (mirrors activity-model.ts).
 */

import { CodocLifecycle } from './status-presentation';

// How long an on-disk `realizing` state is trusted without a fresh status.json
// write before a passive reader treats the pass as dead. Parity with Python
// `status.REALIZING_LEASE_SECONDS` (300s) — the two clocks MUST agree or the IDE
// and the daemon would disagree about when a crashed pass expires.
export const REALIZING_LEASE_MS = 300_000;

export interface DisplayStatus {
    state: CodocLifecycle;
    pending: number;
    detail: string;
}

/**
 * Number of queued directives in realize.md text (0 when empty/absent).
 * Mirrors Python `status._realize_queue_size`: one `### ` heading per directive,
 * flooring to 1 for a non-empty queue with no headings.
 */
export function realizeQueueSize(text: string): number {
    if (!text.trim()) return 0;
    return (text.match(/^### /gm) || []).length || 1;
}

/**
 * Lease-decay a displayed status. A `realizing` state whose status.json write is
 * older than `REALIZING_LEASE_MS` is a crashed/cancelled pass → decays to the
 * ground truth (awaiting_impl if `queueSize > 0`, else in_sync). Every other
 * state — and a fresh `realizing` — is returned verbatim.
 *
 * `statusMtimeMs` is status.json's last-modified time (the lease clock). When
 * `undefined` (a caller with no lease info) the raw state is trusted, matching
 * `isAgentActive`'s fallback; production callers always pass it.
 */
export function leaseStatus(
    raw: DisplayStatus,
    statusMtimeMs: number | undefined,
    queueSize: number,
    nowMs: number = Date.now(),
): DisplayStatus {
    if (raw.state !== 'realizing') return raw;
    if (statusMtimeMs === undefined) return raw;
    if ((nowMs - statusMtimeMs) <= REALIZING_LEASE_MS) return raw;
    if (queueSize > 0) {
        return {
            state: 'awaiting_impl',
            pending: queueSize,
            detail: `${queueSize} change(s) ready to implement — run /codoc:sync`,
        };
    }
    return { state: 'in_sync', pending: 0, detail: '' };
}
