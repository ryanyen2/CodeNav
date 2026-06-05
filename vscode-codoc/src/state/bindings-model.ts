/**
 * Read and type the tree.bindings.json sidecar written by codoc/codoc_file/render.py.
 *
 * Schema (version 3):
 *   by_feature    – {feature_id → [{file, symbol}]}
 *   by_file       – {file → [{symbol, feature_id, feature_title}]}
 *   features      – {feature_id → {title, parent_id, realized}}
 *   feature_edges – {feature_id → [{to, weight, kinds}]}        (v2+)
 *   proposals     – in-place overlay payload (v3):
 *       by_feature – {feature_id → {op:'retire'|'amend', event_id, tag, …}}
 *       by_event   – {event_id   → {op:'add'|'move', …}}  (ADD/MOVE ghosts)
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
    // Optional for backward compat — sidecar < v3 has no realization bit (treat
    // absent as realized=true so old trees render normally).
    realized?: boolean;
}

export interface FeatureEdge {
    to: string;
    weight: number;
    kinds: string[];
}

/** In-place overlay for a RETIRE/AMEND proposal that decorates a live node. */
export interface FeatureProposal {
    op: 'retire' | 'amend';
    event_id: string;
    tag: string;            // "code drift" | "agent reflection" | "agent plan"
    rationale?: string;
    title?: string | null;        // amend: proposed new title
    description?: string | null;  // amend: proposed new description
}

/** Overlay for an ADD/MOVE ghost (also emitted as a text hunk). */
export interface EventProposal {
    op: 'add' | 'move';
    tag: string;
    rationale?: string;
    parent_id?: string | null;
    feature_id?: string | null;   // move: the node being moved
    title?: string | null;
    description?: string | null;
}

export interface ProposalsMap {
    by_feature: Record<string, FeatureProposal>;
    by_event: Record<string, EventProposal>;
    /** parent_id ("" = top level) → ADD/MOVE event ids landing under it, so an
     *  Accept/Reject affordance can be anchored at the destination parent node. */
    by_parent?: Record<string, string[]>;
}

export interface SidecarData {
    version: number;
    by_feature: Record<string, SymbolEntry[]>;
    by_file: Record<string, FileEntry[]>;
    features: Record<string, FeatureMeta>;
    // Optional for backward compat — sidecar version 1 has no edges.
    feature_edges?: Record<string, FeatureEdge[]>;
    // Optional for backward compat — sidecar < v3 has no proposals overlay.
    proposals?: ProposalsMap;
}

/** Return an empty sidecar (used when the file hasn't been created yet). */
export function emptySidecar(): SidecarData {
    return { version: 3, by_feature: {}, by_file: {}, features: {}, proposals: { by_feature: {}, by_event: {} } };
}

/** The in-place overlay proposal for a feature, if any (retire/amend). */
export function proposalForFeature(sidecar: SidecarData, featureId: string): FeatureProposal | undefined {
    return sidecar.proposals?.by_feature[featureId];
}

/** True when the feature is an accepted plan placeholder with no code yet. */
export function isUnrealized(sidecar: SidecarData, featureId: string): boolean {
    return sidecar.features[featureId]?.realized === false;
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

/**
 * Directed view of the feature graph, keeping edge direction + kinds (which
 * `featureAdjacency` discards). `feature_edges[src] = [{to, …}]` means src
 * call/imports `to`, i.e. src *depends on* `to`. So:
 *   out[src] = features src depends on   (rel 'depends')
 *   in[dst]  = features that depend on dst (rel 'used by'), with `to` = the dependant
 */
export interface DirectedEdges {
    out: Map<string, FeatureEdge[]>;
    in: Map<string, FeatureEdge[]>;
}

export function directedEdges(sidecar: SidecarData): DirectedEdges {
    const out = new Map<string, FeatureEdge[]>();
    const inn = new Map<string, FeatureEdge[]>();
    const push = (m: Map<string, FeatureEdge[]>, key: string, edge: FeatureEdge): void => {
        const list = m.get(key);
        if (list) list.push(edge);
        else m.set(key, [edge]);
    };
    for (const [src, edges] of Object.entries(sidecar.feature_edges ?? {})) {
        for (const e of edges) {
            if (e.to === src) continue; // drop self-loops
            push(out, src, { to: e.to, weight: e.weight, kinds: e.kinds });
            push(inn, e.to, { to: src, weight: e.weight, kinds: e.kinds });
        }
    }
    return { out, in: inn };
}
