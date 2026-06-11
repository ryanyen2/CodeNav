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
        // Source 1: explicit feature_ids in the touched entry
        for (const fid of entry.feature_ids) {
            featureIds.add(fid);
        }

        // Source 2: resolve via sidecar (works even when feature_ids aren't populated)
        if (sidecar) {
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
        for (const fid of entry.feature_ids) mark(fid, mode);
        if (sidecar) {
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
