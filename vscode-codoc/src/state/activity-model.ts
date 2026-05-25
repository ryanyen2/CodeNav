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

export interface ActivityData {
    epoch?: ActivityEpoch;
    touched?: Record<string, TouchedEntry>;
    recent?: RecentEntry[];
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

/** Collect the union of all feature_ids mentioned in touched entries. */
export function computeActiveFeatureIds(data: ActivityData): Set<string> {
    const ids = new Set<string>();
    for (const entry of Object.values(data.touched ?? {})) {
        for (const fid of entry.feature_ids) {
            ids.add(fid);
        }
    }
    return ids;
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
