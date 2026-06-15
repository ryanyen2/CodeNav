/**
 * Read and type the tree.index.json cross-reference registry written by
 * codoc/codoc_file/render.py:_compute_registry. Pure derived state — emitted
 * each loop pass next to tree.bindings.json.
 *
 * Schema (version 1):
 *   features – {feature_id → {title, parent_id}}
 *   bindings – [{file, symbol_path, feature_id}]
 *   refs     – [{feature_id, label, file, symbol|null, resolved}]
 *
 * Python already does the (leaf-tolerant) resolution and bakes the authoritative
 * `resolved` flag into each ref, so the extension never re-derives — it just
 * consumes. Kept vscode-import-free so the testable core runs under vitest,
 * exactly like bindings-model.ts. The file IO lives in the (vscode-free) Node
 * `fs`/`path` loader below.
 */

import * as fs from 'fs';
import * as path from 'path';

export interface RegistryFeature {
    title: string;
    parent_id: string | null;
}

export interface RegistryBinding {
    file: string;
    symbol_path: string;
    feature_id: string;
}

export interface RegistryRef {
    feature_id: string;
    label: string;
    file: string;
    symbol: string | null;
    resolved: boolean;
}

export interface RegistryData {
    version: number;
    features: Record<string, RegistryFeature>;
    bindings: RegistryBinding[];
    refs: RegistryRef[];
}

/**
 * Load .codoc/tree.index.json. Tolerant — a missing or corrupt file returns null
 * (never throws), mirroring how the bindings sidecar is read in workspace-state.
 */
export function loadRegistry(rootDir: string): RegistryData | null {
    try {
        const raw = fs.readFileSync(path.join(rootDir, '.codoc', 'tree.index.json'), 'utf-8');
        const data = JSON.parse(raw) as RegistryData;
        // Minimal shape guard — a JSON blob missing `refs` would crash callers.
        if (!Array.isArray(data.refs)) return null;
        return data;
    } catch {
        return null;
    }
}

/**
 * Whether the inline `codoc:` ref `(file, symbol)` resolves to a binding.
 *
 * The registry's `refs` array already carries the authoritative `resolved` flag
 * (Python did the leaf-tolerant resolution); this is a pure lookup, NOT a
 * re-derivation. Policy:
 *   - a matching ref with `resolved: false` → false (a dead ref, strike it);
 *   - a matching ref with `resolved: true`  → true;
 *   - an UNKNOWN ref (registry null, or no `refs` entry for this file/symbol) →
 *     true. We never strike something the registry doesn't know about — absence
 *     of evidence is not evidence of a dead ref (graceful when the registry is
 *     missing/stale or the description text is ahead of the last pass).
 *
 * `symbol` is matched as the raw authored symbol (file-only refs pass `null`);
 * comparison is against the ref's recorded `file`/`symbol`, so it mirrors the
 * exact ref shape Python resolved.
 */
export function isRefResolved(
    registry: RegistryData | null,
    file: string,
    symbol: string | null,
): boolean {
    if (!registry) return true;
    const sym = symbol && symbol.length > 0 ? symbol : null;
    for (const ref of registry.refs) {
        if (ref.file === file && ref.symbol === sym) {
            return ref.resolved;
        }
    }
    // Unknown to the registry → treat as resolved (don't strike).
    return true;
}
