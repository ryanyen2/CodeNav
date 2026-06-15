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
 * consumes. Kept vscode-import-free AND node-import-free (no `fs`/`path`) so the
 * pure core bundles into BOTH the extension host AND the browser webview (U4's
 * hover cards run client-side). The Node `fs`/`path` loader lives in the sibling
 * `registry-loader.ts`; tests + the webview import only the pure core here.
 */

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

/* ────────────────────────────────────────────────────────────────────────────
 * Hover-card lookup precompute (U4 — webview).
 *
 * The webview can't read files or call Python, so the host precomputes every card
 * a reader could hover and ships them by lookup key in the DocPayload. This is the
 * pure assembly the host's `buildPayload` calls — kept here (vscode-free) so the
 * unit test exercises the exact production path under vitest.
 *
 *   - byRef:     one card per registry ref, keyed by its exact hovered target
 *                (`file#symbol`, or bare `file` for a file-only ref). This is the
 *                key a `codeRef` chip uses (`data-file` + `data-symbol`). The
 *                owning feature's description (threaded via `descOf`) yields the
 *                gist — the sidecar carries no description, so the host supplies it.
 *   - byFeature: one card per realized/known feature, keyed by feature id — what a
 *                feature-title link hover resolves. Built by resolving the feature's
 *                first/representative binding (so it shares the resolveCard contract)
 *                or, for a binding-less placeholder, a direct HoverCard.
 * ──────────────────────────────────────────────────────────────────────────── */

export interface HoverCards {
    byRef: Record<string, ResolvedCard>;
    byFeature: Record<string, ResolvedCard>;
}

/** The hovered-target key for a ref: `file#symbol`, or bare `file` when symbol-less.
 *  Matches the `codeRef` chip's `data-file` / `data-symbol` lookup in the webview. */
export function refKey(file: string, symbol: string | null): string {
    const sym = symbol && symbol.length > 0 ? symbol : null;
    return sym ? `${file}#${sym}` : file;
}

/**
 * Precompute the tier-1 hover cards for every ref + feature. Pure over the
 * registry + bindings sidecar; `descOf(fid)` threads the owning feature's
 * description so the card carries a gist (the sidecar has none). No vscode, no
 * Python.
 */
export function buildHoverCards(
    registry: RegistryData | null,
    sidecar: SidecarData,
    descOf: (fid: string) => string | null | undefined,
): HoverCards {
    const byRef: Record<string, ResolvedCard> = {};
    const byFeature: Record<string, ResolvedCard> = {};

    // A card per registry ref (what a codeRef chip hovers).
    for (const ref of registry?.refs ?? []) {
        const key = refKey(ref.file, ref.symbol);
        if (key in byRef) continue; // first ref wins (a target may be cited twice)
        // Thread the OWNING feature's description so a resolved feature card gets a
        // gist; resolveCard re-derives the owner, but the description is keyed by the
        // ref's recorded feature_id (the feature that authored the citation).
        byRef[key] = resolveCard(registry, sidecar, ref.file, ref.symbol, descOf(ref.feature_id));
    }

    // A card per feature (what a feature-title link hovers). Resolve through the
    // feature's first binding so file-only / nested symbols share the contract;
    // a binding-less (unrealized) feature gets a direct HoverCard.
    const featureIds = new Set<string>([
        ...Object.keys(sidecar.features),
        ...Object.keys(registry?.features ?? {}),
    ]);
    for (const fid of featureIds) {
        const meta = sidecar.features[fid];
        const regMeta = registry?.features[fid];
        const title = meta?.title ?? regMeta?.title ?? fid;
        const binds = bindingsForFeature(sidecar, fid);
        const gist = firstSentence(descOf(fid));
        if (binds.length > 0) {
            const b = binds[0];
            const card = resolveCard(registry, sidecar, b.file, b.symbol, descOf(fid));
            // resolveCard resolves the owner by (file, symbol); for a feature whose
            // first binding it owns this is exactly `fid`. Use it directly when it
            // resolved to a feature card, else fall back to a synthesized card.
            byFeature[fid] = card.resolved && card.kind === 'feature'
                ? card
                : {
                    resolved: true, kind: 'feature', title, gist,
                    bindingCount: binds.length, ownerFeatureId: fid,
                    unrealized: isUnrealized(sidecar, fid) && binds.length === 0,
                };
        } else {
            byFeature[fid] = {
                resolved: true, kind: 'feature', title, gist,
                bindingCount: 0, ownerFeatureId: fid,
                unrealized: isUnrealized(sidecar, fid),
            };
        }
    }

    return { byRef, byFeature };
}
