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

/** Parse the text content of activity.json into ActivityData. Returns {} on any error. */
export function parseActivity(text: string): ActivityData {
    if (!text.trim()) return {};
    try {
        return JSON.parse(text) as ActivityData;
    } catch {
        return {};
    }
}

/** Returns true iff an agent session is currently open. */
export function isAgentActive(data: ActivityData): boolean {
    return data.epoch?.open === true;
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
): number[] {
    if (!isAgentActive(data)) return [];

    const featureIds = new Set<string>();

    for (const [filePath, entry] of Object.entries(data.touched ?? {})) {
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
): Map<string, 'write' | 'read'> {
    const modes = new Map<string, 'write' | 'read'>();
    if (!isAgentActive(data)) return modes;

    const mark = (fid: string, mode: 'write' | 'read'): void => {
        if (modes.get(fid) === 'write') return; // write wins, never downgrade
        modes.set(fid, mode);
    };

    for (const [filePath, entry] of Object.entries(data.touched ?? {})) {
        const mode: 'write' | 'read' = entry.mode === 'write' ? 'write' : 'read';
        if (entry.feature_ids.length) {
            for (const fid of entry.feature_ids) mark(fid, mode);
        } else if (sidecar) {
            for (const fe of entriesForFile(sidecar, filePath)) mark(fe.feature_id, mode);
        }
    }
    return modes;
}

/** Per-feature reflection phase from activity.json (empty if absent). */
export function featurePhases(data: ActivityData): Map<string, FeaturePhase> {
    const m = new Map<string, FeaturePhase>();
    for (const [fid, entry] of Object.entries(data.features ?? {})) {
        if (entry && entry.phase) m.set(fid, entry.phase);
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
): Map<string, { label: string; done: boolean }[]> {
    const out = new Map<string, { label: string; done: boolean }[]>();
    if (!isAgentActive(data)) return out;

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

    const recent = data.recent ?? [];
    if (recent.length) {
        const labelsByFid = new Map<string, string[]>();
        for (const r of recent) {                       // chronological
            const label = `${toolVerb(r.tool)} ${baseName(r.file)}`;
            for (const fid of fidsFor(r.file, r.feature_ids)) {
                const list = labelsByFid.get(fid) ?? labelsByFid.set(fid, []).get(fid)!;
                if (list[list.length - 1] !== label) list.push(label);  // collapse consecutive dupes
            }
        }
        for (const [fid, labels] of labelsByFid) {
            const trimmed = labels.slice(-MAX_STEPS);
            out.set(fid, trimmed.map((label, i) => ({ label, done: i < trimmed.length - 1 })));
        }
        return out;
    }

    // Fallback: derive a step per touched file (no event log available).
    const byFid = new Map<string, string[]>();
    for (const [file, entry] of Object.entries(data.touched ?? {})) {
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
