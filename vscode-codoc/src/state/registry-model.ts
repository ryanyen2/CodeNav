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
import {
    SidecarData,
    bindingsForFeature,
    entriesForFile,
    isUnrealized,
} from './bindings-model';

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

/* ────────────────────────────────────────────────────────────────────────────
 * Hover-preview card resolution (U3).
 *
 * `resolveCard` turns an inline `codoc:` ref into the tier-1 preview a hover (or,
 * later, the webview popover) renders. It is a PURE function over the already-
 * loaded registry + bindings sidecar — no vscode imports, no host→Python — so it
 * runs under vitest and both reading surfaces share one contract.
 *
 * Three shapes:
 *   - HoverCard      — a symbol ref resolving to one owning feature.
 *   - FileOwnersCard — a file-only ref (no `#symbol`); enumerates the file's
 *                      owning features rather than picking one arbitrarily.
 *   - DeadRef        — the registry marks the ref unresolved (a rotted link).
 *
 * Card-state fields the caller must render identically (the "do-not-invent" set):
 *   - gist === null      → caller shows a muted "No description yet". The sidecar
 *                          carries only title/parent/realized, so there is no
 *                          description to derive a gist from yet; null is normal.
 *   - unrealized === true → an accepted plan placeholder (realized=false) with
 *                          zero bindings. Caller suppresses the count and shows a
 *                          "plan" marker (shape = kind, not a new colour).
 *   - bindingCount        → from `bindingsForFeature`; surfaced as "N refs".
 * ──────────────────────────────────────────────────────────────────────────── */

export interface HoverCard {
    resolved: true;
    kind: 'feature';
    title: string;
    /** First sentence of the owning feature's description, or null when the
     *  sidecar has no description (caller shows "No description yet"). */
    gist: string | null;
    bindingCount: number;
    ownerFeatureId: string;
    /** Accepted plan placeholder (realized=false, zero bindings): suppress the
     *  count, show a "plan" marker. */
    unrealized: boolean;
}

export interface FileOwnersCard {
    resolved: true;
    kind: 'file';
    file: string;
    owners: { featureId: string; title: string }[];
}

export interface DeadRef {
    resolved: false;
    /** The broken `file` or `file#symbol` the reader hovered. */
    target: string;
}

export type ResolvedCard = HoverCard | FileOwnersCard | DeadRef;

/** Leaf of a (possibly qualified) symbol path: drop the `file::` qualifier and
 *  the `Class.` nesting — mirrors `completion.ts:leaf` + `openRef`'s leaf rule. */
function symbolLeaf(symbolPath: string): string {
    const afterFile = symbolPath.includes('::') ? symbolPath.split('::').pop()! : symbolPath;
    return afterFile.includes('.') ? afterFile.split('.').pop()! : afterFile;
}

/** Whether an authored ref leaf matches a binding's `symbol_path` within a file
 *  — leaf/suffix-tolerant, the same rule the Python registry used to resolve. */
function refMatchesBinding(refSymbol: string, symbolPath: string): boolean {
    if (refSymbol === symbolPath) return true;
    const leaf = refSymbol.includes('::') ? refSymbol.split('::').pop()! : refSymbol;
    // Suffix match on the qualified path's tail (handles authored `Class.method`),
    // then a bare-leaf match (authored `method` → `file.py::Class.method`).
    return symbolPath.endsWith(`.${leaf}`) || symbolPath.endsWith(`::${leaf}`) || symbolLeaf(symbolPath) === leaf;
}

/** First sentence of a description (up to the first `. ` / newline), trimmed.
 *  Returns null for empty/blank input so the caller renders "No description yet". */
function firstSentence(text: string | null | undefined): string | null {
    if (!text) return null;
    const trimmed = text.trim();
    if (!trimmed) return null;
    const m = /^(.*?[.!?])(?:\s|$)/s.exec(trimmed);
    const sentence = (m ? m[1] : trimmed.split(/\r?\n/)[0]).trim();
    return sentence.length > 0 ? sentence : null;
}

/**
 * Resolve a `codoc:` ref to its tier-1 preview card. Pure: reads only the
 * registry (for the authoritative resolved flag) and the bindings sidecar (for
 * title / binding count / realized bit). `symbol` is the authored leaf (file-only
 * refs pass null/'').
 *
 * `description` is optional: the bindings sidecar has no description today, so
 * gist is null unless a caller threads one in. The signature accepts it now so
 * the contract is stable when descriptions land in derived state.
 */
export function resolveCard(
    registry: RegistryData | null,
    sidecar: SidecarData,
    file: string,
    symbol: string | null,
    description?: string | null,
): ResolvedCard {
    const sym = symbol && symbol.length > 0 ? symbol : null;
    const target = file + (sym ? `#${sym}` : '');

    // Dead per the registry → no card. (Unknown refs are treated as live by
    // isRefResolved; we never strike what the registry doesn't know about.)
    if (!isRefResolved(registry, file, sym)) {
        return { resolved: false, target };
    }

    // File-only ref → enumerate every owning feature; never pick one arbitrarily.
    if (!sym) {
        const owners = entriesForFile(sidecar, file).map(e => ({
            featureId: e.feature_id,
            title: e.feature_title,
        }));
        // De-dup (a file owns one feature per symbol, but several symbols may
        // share a feature) preserving first-seen order.
        const seen = new Set<string>();
        const deduped = owners.filter(o => (seen.has(o.featureId) ? false : (seen.add(o.featureId), true)));
        return { resolved: true, kind: 'file', file, owners: deduped };
    }

    // Symbol ref → find the owning feature. The registry's `refs` entry carries
    // the authoritative `feature_id` (the feature whose description authored the
    // ref) and works even for an unrealized placeholder that owns no binding yet.
    // Fall back to the binding table, then the sidecar by_file (registry-less).
    let ownerFeatureId: string | null = null;
    if (registry) {
        for (const r of registry.refs) {
            if (r.file === file && r.symbol === sym) {
                ownerFeatureId = r.feature_id;
                break;
            }
        }
        if (!ownerFeatureId) {
            for (const b of registry.bindings) {
                if (b.file === file && refMatchesBinding(sym, b.symbol_path)) {
                    ownerFeatureId = b.feature_id;
                    break;
                }
            }
        }
    }
    if (!ownerFeatureId) {
        for (const e of entriesForFile(sidecar, file)) {
            if (refMatchesBinding(sym, e.symbol)) {
                ownerFeatureId = e.feature_id;
                break;
            }
        }
    }

    // Resolved per the registry but no owning feature found (e.g. registry-less
    // unknown ref) → present as dead rather than an empty card.
    if (!ownerFeatureId) {
        return { resolved: false, target };
    }

    const meta = sidecar.features[ownerFeatureId];
    const title = meta?.title ?? registry?.features[ownerFeatureId]?.title ?? ownerFeatureId;
    const bindingCount = bindingsForFeature(sidecar, ownerFeatureId).length;
    const unrealized = isUnrealized(sidecar, ownerFeatureId) && bindingCount === 0;

    return {
        resolved: true,
        kind: 'feature',
        title,
        gist: firstSentence(description),
        bindingCount,
        ownerFeatureId,
        unrealized,
    };
}
