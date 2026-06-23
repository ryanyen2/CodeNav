/**
 * Read and type the tree.bindings.json sidecar written by codoc/codoc_file/render.py.
 *
 * Schema (version 5):
 *   by_feature    – {feature_id → [{file, symbol}]}
 *   by_file       – {file → [{symbol, feature_id, feature_title}]}
 *   features      – {feature_id → {title, parent_id, realized, pitch}}
 *   feature_edges – {feature_id → [{to, weight, kinds}]}        (v2+)
 *   proposals     – in-place overlay payload (v3):
 *       by_feature – {feature_id → {op:'retire'|'amend', event_id, tag, …}}
 *       by_event   – {event_id   → {op:'add'|'move', …}}  (ADD/MOVE ghosts)
 *   changes          – recent applied events with provenance (v4)
 *   holds            – doc-wins hold set (v4)
 *   pitch            – derived one-line pitch on each FeatureMeta (v5)
 *   feature_kind     – Diátaxis-lite kind hint per feature (v5)
 *   feature_see_also – top-N coupled neighbours per feature (v5, data only)
 *   feature_drift    – loop-computed per-feature drift/trust signal (v5,
 *                      questioned|binding-lost; `followed` is absent → no badge)
 *
 * New slices are optional — the reader keys on field presence, not the version
 * literal, so older sidecars keep parsing.
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
    // A1: the authoritative named lifecycle state (planned|active|retired).
    // Optional so older sidecars (which carried only `realized`) still parse.
    lifecycle?: FeatureLifecycle;
    // Optional for backward compat — sidecar < v3 has no realization bit (treat
    // absent as realized=true so old trees render normally). Derived view of
    // `lifecycle`; prefer `lifecycle` for new code.
    realized?: boolean;
    // v5: a derived one-line pitch (first sentence of the description, refs
    // flattened, else the title). Python derives it; the TS side only reads it.
    // Optional so older (< v5) sidecars still parse.
    pitch?: string;
    // v6: the hand-authored node's client id (KTD8), present only for a feature
    // minted from a webview ADD. Lets the host map a freshly-minted fid back to the
    // exact in-progress node (localId→fid) instead of guessing by title/order.
    local_id?: string;
}

/** A1: the named, persistent lifecycle state on a feature. */
export type FeatureLifecycle = 'planned' | 'active' | 'retired';

/** The single mid-flight phase per feature (Proposal B `feature_phase` slice):
 *  the one place "where is this feature in its lifecycle?" is named, from which
 *  holds / drift / resolution are derived. `synced` features are absent (no dot).
 *  Doc-wins is applied at projection time (a held feature reads drafting/queued,
 *  never drifted/divergent). */
export type FeaturePhase =
    | 'retired' | 'drafting' | 'queued' | 'divergent' | 'drifted' | 'planned';

export interface FeatureEdge {
    to: string;
    weight: number;
    kinds: string[];
}

/** A derived Diátaxis-lite kind hint per feature (v5, `feature_kind` slice). A pure
 *  structural heuristic, NOT a model field: `overview` = a binding-less realized theme
 *  parent (has children); `reference` = a code-bound feature; `unclassified` = a
 *  binding-less leaf (just-detached / pre-attach placeholder); `retired` = a tombstoned
 *  node (suppressed in the UI). Rendered as a small chip below the feature title. */
export type FeatureKind = 'overview' | 'reference' | 'unclassified' | 'retired';

/** The loop-computed per-feature drift/trust signal (v5, `feature_drift` slice). Computed
 *  in the loop pass that re-indexes (render has no live index), typed and doc-wins-aware:
 *  `questioned` = a realized feature owns a bound chunk whose code changed and whose prose
 *  was not amended this pass (the description may be stale); `binding-lost` = a realized
 *  feature lost its last binding. `followed` (the common case) is NEVER recorded — its
 *  badge is the ABSENCE of an entry. Held + unrealized features are excluded. Rendered as
 *  a quiet shape/glyph badge (NOT a new hue — colour stays reserved for direction). */
export type FeatureDrift = 'questioned' | 'binding-lost';

/** Per-held-feature detail for the in-situ "pending intent" decoration (v5,
 *  `hold_detail` slice): the queued directive's kind + a plain-language gloss of what
 *  the agent will do. A subset of `holds` — only features with a queued realize
 *  directive carry it; a feature held by a live intent alone gets the plain rail. */
export interface HoldDetail {
    /** NodeOpKind value of the queued directive (amend | add_node | retire_node | steer). */
    kind: string;
    /** One-line plain-language summary of the work — the pending rail's hover title. */
    intent: string;
    /** The feature's description BEFORE this edit (AMEND only) — the IDE diffs it against
     *  the live text to underline what the author changed. Empty for ADD/RETIRE/steer. */
    baseline?: string;
}

/** One See-Also neighbour (v5, `feature_see_also` slice): a coupled feature ranked by
 *  coupling `weight`, with the edge `kinds` (calls/imports) summarised as a one-line
 *  `rationale`. Emitted as DATA ONLY — the Connections panel already surfaces coupled
 *  features (Depends-on / Used-by), so there is no second See-Also UI section; this slice
 *  exists for completeness + future consumers and is never a `> …` steering line. */
export interface SeeAlsoEntry {
    to: string;
    weight: number;
    kinds: string[];
    rationale: string;
}

/** In-place overlay for a RETIRE/AMEND proposal that decorates a live node. */
export interface FeatureProposal {
    op: 'retire' | 'amend';
    event_id: string;
    tag: string;            // "code drift" | "agent reflection" | "agent plan"
    rationale?: string;
    title?: string | null;        // amend: proposed new title
    description?: string | null;  // amend: proposed new description
    // v4 provenance ledger ("" / absent = legacy/unknown).
    actor?: string;               // "human" | agent id | "loop"
    mode?: string;                // "pen" | "suggest" | "auto"
    caused_by?: string;           // directive (d-…) / event / suggestion id
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
    // v4 provenance ledger ("" / absent = legacy/unknown).
    actor?: string;
    mode?: string;
    caused_by?: string;
}

/** One entry of the v4 `changes` feed — a recent APPLIED event with provenance,
 *  newest first. How the IDE learns who last changed each feature (pencil-stamp
 *  AI prose) and which directive a reflection cascade implements. */
export interface ChangeEntry {
    event_id: string;
    at: string;          // HLC string (lexicographically sortable)
    kind: string;        // attach|detach|refresh|amend|add_node|move_node|retire_node
    feature_id: string;  // "" when the op carried none
    actor: string;
    mode: string;
    caused_by: string;
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
    // v4: recent applied events with provenance (newest first).
    changes?: ChangeEntry[];
    // v4: features with pending doc-ahead intent (doc-wins hold set).
    holds?: string[];
    // v5: per-held-feature {kind, intent} for the pending-intent decoration's hover.
    // A subset of `holds` (only features with a queued directive). Absent ⇒ none.
    hold_detail?: Record<string, HoldDetail>;
    // v5: a derived Diátaxis-lite kind hint per feature (overview/reference/…).
    feature_kind?: Record<string, FeatureKind>;
    // v5: top-N coupled neighbours per feature (data only — the Connections panel
    // already surfaces coupled features, so no second See-Also UI section).
    feature_see_also?: Record<string, SeeAlsoEntry[]>;
    // v5: loop-computed per-feature drift/trust signal (questioned | binding-lost).
    // `followed` features are absent (no badge). Re-emitted from drift.json.
    feature_drift?: Record<string, FeatureDrift>;
    // v5 (U5): per-feature realize-divergence — a feature changed BEYOND a
    // directive's target during a realization (reason "scope"), surfaced for
    // "review what the AI did" (F3). Re-emitted from resolution.json, filtered to
    // features whose surfaced proposal is still pending. Faithful realizations are
    // absent (their badge just clears).
    feature_resolution?: Record<string, string>;
    // Proposal B: the SINGLE mid-flight projection — feature_id → phase. The one
    // source the slices above are thin views of; `synced` features are absent.
    feature_phase?: Record<string, FeaturePhase>;
    // v6: typed-media blocks per feature (diagram | image | latex | url | …),
    // persistent only (transient blocks ride the steers channel; prose is the
    // feature description = block-zero, not here). A feature with no typed media is
    // absent. The reader keys on presence, so a v5 sidecar (no `blocks`) still parses.
    blocks?: Record<string, BlockEntry[]>;
}

/** A typed-media block on a feature (v6, `blocks` slice). `content` is opaque —
 *  its schema belongs to the plugin named by `kind` (mermaid for diagram, a URL
 *  for url, an attachment ref for image). `id` is stable (KTD8): the host MUST
 *  preserve it across edits (move/type-change/undo) so identity is never inferred
 *  from content. */
export interface BlockEntry {
    id: string;
    kind: string;
    content: string;
    lifecycle: 'persistent' | 'transient';
    provenance: 'human' | 'agent' | 'derived';
    ord: number;
}

/** The derived kind hint for a feature, if any (v5). Suppressed/retired tags are
 *  filtered by the renderer (no chip), so callers get the raw slice value here. */
export function kindForFeature(sidecar: SidecarData, featureId: string): FeatureKind | undefined {
    return sidecar.feature_kind?.[featureId];
}

/** Top-N coupled neighbours for a feature (v5). Empty when the feature has no edges. */
export function seeAlsoForFeature(sidecar: SidecarData, featureId: string): SeeAlsoEntry[] {
    return sidecar.feature_see_also?.[featureId] ?? [];
}

/** The loop-computed drift/trust signal for a feature, if any (v5). `undefined`
 *  means `followed` (the common case) — no badge. Held + unrealized features are
 *  excluded loop-side, so they are always `undefined` here. */
export function driftForFeature(sidecar: SidecarData, featureId: string): FeatureDrift | undefined {
    return sidecar.feature_drift?.[featureId];
}

/** Features flagged as a DIVERGENT realization (U5): a code-implying edit was
 *  realized, but the agent changed THIS feature beyond the one you edited — surfaced
 *  for "review what the AI did" (F3). `{feature_id → reason}`; empty = nothing to
 *  review (every realization so far was faithful). Tolerant default for old sidecars. */
export function divergentFeatures(sidecar: SidecarData): Record<string, string> {
    return sidecar.feature_resolution ?? {};
}

/** Features "awaiting AI realization" — the daemon-computed doc-wins hold set
 *  (`sidecar.holds` = live doc-ahead intents ∪ queued realize directives; see
 *  codoc/loop/edits.py:hold_set). In the single-surface model (U3) a human's
 *  code-implying commit makes Loop B mint a directive, which lands the feature
 *  here; a faithful realization clears it (the badge auto-resolves). This is the
 *  SOLE source for the "being realized" badge — no client-side classification.
 *  Tolerant: a sidecar from before v4 has no `holds`, so default to none. */
export function heldFeatures(sidecar: SidecarData): string[] {
    return sidecar.holds ?? [];
}

/** Per-held-feature detail (kind + intent gloss) for the in-situ pending-intent
 *  decoration. A subset of `heldFeatures`: only features with a queued directive carry
 *  detail; a feature held by a live intent alone is absent (it still gets the plain
 *  rail). Tolerant: a sidecar before the `hold_detail` slice yields an empty map. */
export function heldDetail(sidecar: SidecarData): Record<string, HoldDetail> {
    return sidecar.hold_detail ?? {};
}

/** Latest applied agent-authored AMEND per feature (fid → agent actor id), from
 *  the v4 changes feed. Drives the pencil re-stamp: when a description changed
 *  under the saved doc AND this map names an agent, the fresh text is inked as
 *  that agent's pencil instead of resetting to plain. */
export function agentAmendsByFeature(sidecar: SidecarData): Map<string, string> {
    const out = new Map<string, string>();
    const decided = new Set<string>();
    for (const c of sidecar.changes ?? []) {  // newest first — the latest amend decides
        if (c.kind !== 'amend' || !c.feature_id || decided.has(c.feature_id)) continue;
        decided.add(c.feature_id);
        // Any non-human machine actor counts: an MCP-reflecting agent
        // ("claude-code", "codex", …) or Loop A's own LLM pass ("loop"). A newer
        // HUMAN amend shadows an older agent one — the prose is theirs again.
        if (c.actor && c.actor !== 'human') out.set(c.feature_id, c.actor);
    }
    return out;
}

/** Map a hand-authored node's client id → its minted fid, from the sidecar (v6).
 *  The host uses this to patch a freshly-minted fid onto the exact in-progress node
 *  (replacing the fragile title/order matching that spawned duplicate/orphan adds). */
export function mintedByLocalId(sidecar: SidecarData): Record<string, string> {
    const out: Record<string, string> = {};
    for (const [fid, meta] of Object.entries(sidecar.features)) {
        if (meta?.local_id) out[meta.local_id] = fid;
    }
    return out;
}

/** Return an empty sidecar (used when the file hasn't been created yet). */
export function emptySidecar(): SidecarData {
    return { version: 6, by_feature: {}, by_file: {}, features: {}, proposals: { by_feature: {}, by_event: {} } };
}

/** The typed-media blocks for a feature (v6), normalized + ordered by `ord` — the
 *  canonical host block view (mirrors codoc/blocks/conformance.py:canonical_block_view
 *  so every host renders the SAME blocks). Empty for a feature with no typed media
 *  or a pre-v6 sidecar. */
export function blocksForFeature(sidecar: SidecarData, featureId: string): BlockEntry[] {
    const raw = sidecar.blocks?.[featureId] ?? [];
    return raw
        .map((e) => ({
            id: e.id ?? '',
            kind: e.kind ?? '',
            content: e.content ?? '',
            lifecycle: e.lifecycle ?? 'persistent',
            provenance: e.provenance ?? 'human',
            ord: e.ord ?? 0,
        }))
        .sort((a, b) => a.ord - b.ord);
}

/** The in-place overlay proposal for a feature, if any (retire/amend). */
export function proposalForFeature(sidecar: SidecarData, featureId: string): FeatureProposal | undefined {
    return sidecar.proposals?.by_feature[featureId];
}

/** True when the feature is an accepted plan placeholder with no code yet.
 *  Prefers the A1 named `lifecycle`; falls back to the legacy `realized` view for
 *  sidecars written before A1. */
export function isUnrealized(sidecar: SidecarData, featureId: string): boolean {
    const meta = sidecar.features[featureId];
    if (meta?.lifecycle) return meta.lifecycle === 'planned';
    return meta?.realized === false;
}

/** A1: the named lifecycle state for a feature (planned|active|retired). Falls
 *  back to deriving it from the legacy `realized` view for pre-A1 sidecars. */
export function lifecycleForFeature(sidecar: SidecarData, featureId: string): FeatureLifecycle {
    const meta = sidecar.features[featureId];
    if (meta?.lifecycle) return meta.lifecycle;
    return meta?.realized === false ? 'planned' : 'active';
}

/** Proposal B: the single mid-flight phase for a feature, if any. `undefined`
 *  means `synced` (the common case) — no dot. Holds / drift / resolution are
 *  thin views of this same projection. */
export function phaseForFeature(sidecar: SidecarData, featureId: string): FeaturePhase | undefined {
    return sidecar.feature_phase?.[featureId];
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
