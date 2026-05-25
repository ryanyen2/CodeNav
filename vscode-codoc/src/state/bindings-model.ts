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

export interface SidecarData {
    version: number;
    by_feature: Record<string, SymbolEntry[]>;
    by_file: Record<string, FileEntry[]>;
    features: Record<string, FeatureMeta>;
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
