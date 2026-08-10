/**
 * activity-model.ts — Pure functions for parsing and querying .codoc/activity.json.
 *
 * This module has no I/O; all reads are done in workspace-state.ts. Keeping it
 * pure makes it trivially testable without a VS Code host.
 */

import { ParsedFeature } from './tree-model';
import { SidecarData, entriesForFile } from './bindings-model';

export interface ActivityEpoch {
    id: string;
    origin: 'interactive' | 'loop_b';
    open: boolean;
    started_at: string | null;
    ended_at: string | null;
    /** W1: the coding agent that owns this epoch (claude-code | codex | …). Absent
     *  on epochs written before per-agent identity — readers fall back to 'claude'. */
    agent?: { id: string };
}

export interface TouchedEntry {
    symbols: string[];
    feature_ids: string[];
    last: string | null;
    mode: 'read' | 'write';
}

export interface RecentEntry {
    tool: string;
    file: string;
    feature_ids: string[];
    at: string;
    phase: string;
    /** W1: an ACTION entry (a Bash test-run / git verb the hook classified) —
     *  `label` renders verbatim in the ribbon; `action` is its kind. File-touch
     *  entries omit both. */
    action?: string;
    label?: string;
}

/** Per-feature reflection phase, written by the hook (editing) + MCP reflect/attach
 *  (reflecting → done). Drives the doc view's skeleton → fill-in animation. */
export type FeaturePhase = 'editing' | 'reflecting' | 'done';

export interface FeaturePhaseEntry {
    phase: FeaturePhase;
    at?: string | null;
}

export interface ActivityData {
    epoch?: ActivityEpoch;
    touched?: Record<string, TouchedEntry>;
    recent?: RecentEntry[];
    // Per-feature streaming phase (activity.json schema ≥ 2). Optional for
    // backward compat — absent ⇒ derive liveness from `touched` modes only.
    features?: Record<string, FeaturePhaseEntry>;
}

// How long `epoch.open === true` is trusted without a fresh activity.json write
// before liveness readers treat it as dead. Claude Code's `Stop` hook — the only
// writer that clears `epoch.open` — does not fire on Esc/kill/closed-window, so
// trusting the raw flag forever shows "agent working…" long after the agent is
// gone. `mtimeMs` (the file's last-modified time) doubles as the lease's
// `last_seen` with no schema change — every hook write touches the file.
export const EPOCH_UI_TTL_MS = 90_000;

// Same failure mode at feature granularity: `features[fid].phase` is only
// cleared by the `Stop` hook resetting the whole block, so an interrupted
// session leaves a feature's skeleton/"editing" animation stuck forever.
export const FEATURE_PHASE_TTL_MS = 120_000;

// How long a single touch/step stays "live" after the agent moved on. Neither
// `recent` nor `touched` is ever pruned by AGE — `recent` rolls by count and
// `touched` only grows — and only the Stop hook clears them. So a file the agent
// read once keeps narrating "reading sessions.py" under every feature bound to it
// for the rest of the session, long after the agent is somewhere else entirely.
export const STEP_TTL_MS = 30_000;

/** The hook writes `at` as a full ISO-8601 stamp (`_now_iso`). Insist on that shape
 *  before trusting it as a clock: `Date.parse` happily reads a bare "3" as the year
 *  2003, which would silently pin the cutoff to a fictional moment and blank the
 *  ribbon. Anything else is treated as unstamped. */
const ISO_DATE = /^\d{4}-\d{2}-\d{2}[T ]/;

function atMs(at: string | null | undefined): number | null {
    if (!at || !ISO_DATE.test(at)) return null;
    const t = Date.parse(at);
    return Number.isNaN(t) ? null : t;
}

/**
 * The instant before which a touch/step counts as stale: STEP_TTL_MS back from the
 * agent's OWN most recent event, not from the wall clock.
 *
 * The agent's clock is the honest one here. A single tool call can run for minutes
 * (a full pytest, a long build) and stays the newest thing that happened, so a
 * wall-clock cutoff would blank the ribbon mid-run and claim the agent left. Read
 * relatively, that same event holds the line open while genuinely older touches age
 * out behind it. Returns null when nothing carries a parseable timestamp (an older
 * activity.json) — no clock, no staleness verdict.
 */
export function stepCutoff(times: (string | null | undefined)[]): number | null {
    let latest: number | null = null;
    for (const t of times) {
        const v = atMs(t);
        if (v !== null && (latest === null || v > latest)) latest = v;
    }
    return latest === null ? null : latest - STEP_TTL_MS;
}

/** An entry with no parseable timestamp is kept — same convention as featurePhases. */
function freshAt(at: string | null | undefined, cutoff: number | null): boolean {
    if (cutoff === null) return true;
    const v = atMs(at);
    return v === null || v >= cutoff;
}

/** The touched entries the agent is still plausibly on — see stepCutoff. */
function liveTouched(data: ActivityData): [string, TouchedEntry][] {
    const all = Object.entries(data.touched ?? {});
    const cutoff = stepCutoff(all.map(([, e]) => e.last));
    return all.filter(([, e]) => freshAt(e.last, cutoff));
}

/** Parse the text content of activity.json into ActivityData. Returns {} on any error. */
export function parseActivity(text: string): ActivityData {
    if (!text.trim()) return {};
    try {
        return JSON.parse(text) as ActivityData;
    } catch {
        return {};
    }
}

/** W1: the role id of the coding agent owning the current epoch — drives the
 *  presence avatar name/tint, the ribbon "who", and (via role) any per-agent
 *  colour. Falls back to 'claude' for epochs written before per-agent identity. */
export function agentRole(data: ActivityData): string {
    return data.epoch?.agent?.id || 'claude';
}

/**
 * Returns true iff an agent session is currently open — a LEASE, not a flag.
 *
 * `mtimeMs` is activity.json's last-modified time (the host reads it via
 * `fs.statSync` alongside the JSON); when provided, `open === true` is only
 * trusted for `EPOCH_UI_TTL_MS` since that write. When omitted (a caller with
 * no lease info, e.g. most existing unit tests) this falls back to trusting
 * the raw flag, matching the pre-lease behavior — production callers should
 * always pass it (see `WorkspaceState.activityMtimeMs`).
 */
export function isAgentActive(data: ActivityData, mtimeMs?: number, nowMs: number = Date.now()): boolean {
    if (data.epoch?.open !== true) return false;
    if (mtimeMs === undefined) return true;
    return (nowMs - mtimeMs) <= EPOCH_UI_TTL_MS;
}

/**
 * Compute the 0-based line numbers (in tree.codoc) of features that are
 * actively being edited by the agent.
 *
 * Sources (union):
 *   1. feature_ids listed directly in each touched entry
 *   2. feature_ids resolved via the sidecar for each touched file path
 */
export function computeActiveFeatureLines(
    data: ActivityData,
    features: ParsedFeature[],
    sidecar: SidecarData | null,
    mtimeMs?: number,
    nowMs: number = Date.now(),
): number[] {
    if (!isAgentActive(data, mtimeMs, nowMs)) return [];

    const featureIds = new Set<string>();

    for (const [filePath, entry] of liveTouched(data)) {
        if (entry.feature_ids.length) {
            // The hook already resolved (and, when a file is shared by several
            // features, narrowed to) the feature(s) actually being touched —
            // trust it rather than re-widening back out via the sidecar.
            for (const fid of entry.feature_ids) featureIds.add(fid);
        } else if (sidecar) {
            // Fallback: no explicit feature_ids (older activity.json) — resolve
            // via the sidecar so liveness still renders.
            for (const fileEntry of entriesForFile(sidecar, filePath)) {
                featureIds.add(fileEntry.feature_id);
            }
        }
    }

    const lines: number[] = [];
    for (const fid of featureIds) {
        const feature = features.find(f => f.id === fid);
        if (feature !== undefined) {
            lines.push(feature.line);
        }
    }

    return lines;
}

/**
 * Map each actively-touched feature id to whether it is being written or only
 * read right now. Empty when no epoch is open. `write` wins over `read` when a
 * feature appears in both (an agent writing a file it also read is "writing").
 *
 * Feature ids come from (a) the touched entry's explicit `feature_ids` and
 * (b) sidecar resolution of the touched file path — same union as
 * `computeActiveFeatureLines`, but preserving the read/write mode.
 */
export function activeFeatureModes(
    data: ActivityData,
    sidecar: SidecarData | null,
    mtimeMs?: number,
    nowMs: number = Date.now(),
): Map<string, 'write' | 'read'> {
    const modes = new Map<string, 'write' | 'read'>();
    if (!isAgentActive(data, mtimeMs, nowMs)) return modes;

    const mark = (fid: string, mode: 'write' | 'read'): void => {
        if (modes.get(fid) === 'write') return; // write wins, never downgrade
        modes.set(fid, mode);
    };

    for (const [filePath, entry] of liveTouched(data)) {
        const mode: 'write' | 'read' = entry.mode === 'write' ? 'write' : 'read';
        if (entry.feature_ids.length) {
            for (const fid of entry.feature_ids) mark(fid, mode);
        } else if (sidecar) {
            for (const fe of entriesForFile(sidecar, filePath)) mark(fe.feature_id, mode);
        }
    }
    return modes;
}

/**
 * Per-feature reflection phase from activity.json (empty if absent).
 *
 * TTL-filtered on each entry's own `at` timestamp (`FEATURE_PHASE_TTL_MS`): only
 * the `Stop` hook clears this block, and it never fires on an interrupted/killed
 * session, so an un-filtered read would show "editing" forever. An entry with no
 * `at` (older activity.json) is kept as-is — no lease info, no staleness verdict.
 */
export function featurePhases(data: ActivityData, nowMs: number = Date.now()): Map<string, FeaturePhase> {
    const m = new Map<string, FeaturePhase>();
    for (const [fid, entry] of Object.entries(data.features ?? {})) {
        if (!entry || !entry.phase) continue;
        if (entry.at) {
            const at = Date.parse(entry.at);
            if (!Number.isNaN(at) && (nowMs - at) > FEATURE_PHASE_TTL_MS) continue;
        }
        m.set(fid, entry.phase);
    }
    return m;
}

// ─── Agent-action steps (P2b — the inline ribbon) ─────────────────────────────

const MAX_STEPS = 5;   // keep the ribbon short; older steps fall off the top

/** A tool call → a human verb. The agent's actual tools, humanised. */
function toolVerb(tool: string): string {
    switch (tool) {
        case 'Edit': case 'Write': case 'MultiEdit': case 'NotebookEdit': return 'editing';
        case 'Read': return 'reading';
        case 'Bash': return 'running';
        case 'Grep': case 'Glob': return 'searching';
        default: return tool.toLowerCase();
    }
}
const baseName = (p: string): string => p.split('/').pop() || p;

/** Build each active feature's ordered action steps for the ribbon. Prefers the
 *  `recent` event log (real tool calls → "editing agent.py"), falling back to the
 *  `touched` set when no event log is present. The LAST step is active; earlier ones
 *  are `done`. Empty when no epoch is open. Pure — unit-tested directly.
 *
 *  AgentStep is structurally `{ label, done }` (see protocol.ts); kept inline here to
 *  avoid a webview→state import cycle. */
export function featureSteps(
    data: ActivityData,
    sidecar: SidecarData | null,
    mtimeMs?: number,
    nowMs: number = Date.now(),
): Map<string, { label: string; done: boolean; kind?: string }[]> {
    const out = new Map<string, { label: string; done: boolean; kind?: string }[]>();
    if (!isAgentActive(data, mtimeMs, nowMs)) return out;

    // Trust an already-resolved (and, for a file shared by several features,
    // already-narrowed) explicit feature_ids list; only fall back to the full
    // sidecar by_file resolution when nothing explicit is present (older
    // activity.json) — otherwise a file bound to several features would union
    // ALL of them back in, re-broadening a hook-narrowed single-feature touch.
    const fidsFor = (file: string, explicit: string[]): Set<string> => {
        if (explicit && explicit.length) return new Set(explicit);
        const s = new Set<string>();
        if (sidecar) for (const fe of entriesForFile(sidecar, file)) s.add(fe.feature_id);
        return s;
    };

    // Only the tail the agent is still on — an event it has moved past must stop
    // narrating under its feature (see stepCutoff).
    const allRecent = data.recent ?? [];
    const recentCutoff = stepCutoff(allRecent.map(r => r.at));
    const recent = allRecent.filter(r => freshAt(r.at, recentCutoff));
    if (recent.length) {
        const labelsByFid = new Map<string, { label: string; kind?: string }[]>();
        for (const r of recent) {                       // chronological
            // W1: an ACTION entry (test run / git verb) carries its own label and
            // is attributed ONLY to the features the hook saw as being edited —
            // never re-broadened through the file→feature fallback (no file).
            const isAction = !!r.label;
            const label = isAction ? r.label! : `${toolVerb(r.tool)} ${baseName(r.file)}`;
            const kind = isAction ? r.action : undefined;
            const fids = isAction ? new Set(r.feature_ids ?? []) : fidsFor(r.file, r.feature_ids);
            for (const fid of fids) {
                const list = labelsByFid.get(fid) ?? labelsByFid.set(fid, []).get(fid)!;
                if (list[list.length - 1]?.label !== label) list.push({ label, kind });  // collapse consecutive dupes
            }
        }
        for (const [fid, labels] of labelsByFid) {
            const trimmed = labels.slice(-MAX_STEPS);
            out.set(fid, trimmed.map((s, i) => ({ ...s, done: i < trimmed.length - 1 })));
        }
        return out;
    }

    // Fallback: derive a step per touched file (no event log available).
    const byFid = new Map<string, string[]>();
    for (const [file, entry] of liveTouched(data)) {
        const verb = entry.mode === 'write' ? 'editing' : 'reading';
        const label = `${verb} ${baseName(file)}`;
        for (const fid of fidsFor(file, entry.feature_ids)) {
            const list = byFid.get(fid) ?? byFid.set(fid, []).get(fid)!;
            if (!list.includes(label)) list.push(label);
        }
    }
    for (const [fid, labels] of byFid) {
        const trimmed = labels.slice(-MAX_STEPS);
        // touched has no ordering signal for "current", so the most recent write stays active
        out.set(fid, trimmed.map((label, i) => ({ label, done: i < trimmed.length - 1 })));
    }
    return out;
}
