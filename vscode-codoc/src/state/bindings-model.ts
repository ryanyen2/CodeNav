/**
 * Read and type the tree.bindings.json sidecar written by codoc/codoc_file/render.py.
 *
 * Schema (version 1):
 *   by_feature  – {feature_id → [{file, symbol}]}
 *   by_file     – {file → [{symbol, feature_id, feature_title}]}
 *   features    – {feature_id → {title, parent_id}}
 */

export interface SymbolEntry {
    file: string;
    symbol: string;
}

export interface FileEntry {
    symbol: string;
    feature_id: string;
    feature_title: string;
}

export interface FeatureMeta {
    title: string;
    parent_id: string | null;
}

export interface FeatureEdge {
    to: string;
    weight: number;
    kinds: string[];
}

export interface SidecarData {
    version: number;
    by_feature: Record<string, SymbolEntry[]>;
    by_file: Record<string, FileEntry[]>;
    features: Record<string, FeatureMeta>;
    // Optional for backward compat — sidecar version 1 has no edges.
    feature_edges?: Record<string, FeatureEdge[]>;
}

/** Return an empty sidecar (used when the file hasn't been created yet). */
export function emptySidecar(): SidecarData {
    return { version: 1, by_feature: {}, by_file: {}, features: {} };
}

/** Look up all feature entries for a given repo-relative file path. */
export function entriesForFile(sidecar: SidecarData, relPath: string): FileEntry[] {
    return sidecar.by_file[relPath] ?? [];
}

/** Look up bindings for a feature by ID. */
export function bindingsForFeature(sidecar: SidecarData, featureId: string): SymbolEntry[] {
    return sidecar.by_feature[featureId] ?? [];
}

/** Build an undirected feature→feature adjacency map from the sidecar's edges. */
export function featureAdjacency(sidecar: SidecarData): Map<string, Set<string>> {
    const adj = new Map<string, Set<string>>();
    for (const [src, edges] of Object.entries(sidecar.feature_edges ?? {})) {
        if (!adj.has(src)) adj.set(src, new Set());
        for (const e of edges) {
            adj.get(src)!.add(e.to);
            if (!adj.has(e.to)) adj.set(e.to, new Set());
            adj.get(e.to)!.add(src);
        }
    }
    return adj;
}
