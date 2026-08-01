"""Feature subtree selection for the LLM context window.

Phase 3 upgrade (ego-graph):
  Seeds = features bound in touched files + changed symbols.
  Ego-expand along internal graph edges (n hops) to pull in cross-file related
  features — a changed function calling something in an untouched file will now
  surface that dependency's feature in the subtree.

  Also builds a CodePlan-style graph context block: direct edge sketches and
  recent events for seed features.  Both are packed into the ``changes`` dict
  before the LLM call rather than as extra arguments, so mock propose functions
  in tests need no signature changes.
"""
from __future__ import annotations

from codoc.loop.diff import ChangeSet
from codoc.store.db import Store


def select_relevant_subtree(
    cs: ChangeSet,
    store: Store,
    *,
    hops: int = 1,
    features=None,
) -> tuple[list[dict], list[dict], dict]:
    """Return ``(subtree, all_titles, context)`` as JSON-ready dicts.

    ``context`` is a dict with keys ``edges`` and ``recent``; callers should
    merge it into the ``changes`` dict under ``"graph"`` before calling propose.
    ``features`` lets the caller pass the pass's already-loaded feature list so
    the every-node-title context doesn't cost a second full table read.
    """
    changed_symbols: set[str] = {
        c.symbol_path for c in (cs.added + cs.modified + cs.removed)
    }
    return select_context(
        store, cs.touched_files(), changed_symbols, hops=hops, features=features
    )


def select_context(
    store: Store,
    files: set[str],
    changed_symbols: set[str] | None = None,
    *,
    hops: int = 1,
    features=None,
) -> tuple[list[dict], list[dict], dict]:
    """Ego-graph relevance selection from a set of touched files (+ symbols).

    The shared core behind Loop A's LLM context and the agent-facing
    ``codoc_context`` MCP tool: seeds = features bound in ``files`` (+ any
    ``changed_symbols``), expanded ``hops`` along internal graph edges, plus
    1-hop parent/child structural context.
    """
    from codoc.graph.query import ego_graph

    changed_symbols = changed_symbols or set()

    # --- seeds ---------------------------------------------------------------
    seed_bindings = store.bindings_in_files(files)
    seed_features: set[str] = {b.feature_id for b in seed_bindings}
    seed_symbols: set[str] = {b.symbol_path for b in seed_bindings}

    # --- ego-expand ----------------------------------------------------------
    all_related = ego_graph(store, seed_symbols | changed_symbols, hops=hops)

    related_features: set[str] = set(seed_features)
    for sym in all_related:
        if "::" not in sym:
            continue
        file, _ = sym.split("::", 1)
        b = store.binding_at(file, sym)
        if b:
            related_features.add(b.feature_id)

    # 1-hop parent + child expansion for structural context
    ids: set[str] = set(related_features)
    for fid in list(related_features):
        f = store.get_feature(fid)
        if f and f.parent_id:
            ids.add(f.parent_id)
        for child in store.children(fid):
            ids.add(child.id)

    # --- subtree -------------------------------------------------------------
    subtree: list[dict] = []
    for fid in sorted(ids):
        f = store.get_feature(fid)
        if not f or f.retired:
            continue
        subtree.append(
            {
                "id": f.id,
                "title": f.title,
                "description": f.description,
                "parent_id": f.parent_id,
                "bindings": [b.symbol_path for b in store.bindings_for_feature(fid)],
            }
        )

    all_titles = [
        {"id": f.id, "title": f.title, "parent_id": f.parent_id}
        for f in (features if features is not None else store.list_features())
    ]

    # Edge sketches anchor on the changed symbols when there are any (Loop A),
    # else on the seed symbols (agent context reads have no change set).
    context = _build_context(changed_symbols or seed_symbols, seed_features, store)
    return subtree, all_titles, context


def _build_context(
    changed_symbols: set[str],
    seed_features: set[str],
    store: Store,
) -> dict:
    """Build the graph context block (CodePlan spatial + temporal)."""
    edges: list[dict] = []
    seen_edges: set[tuple[str, str, str]] = set()

    for sym in changed_symbols:
        for e in store.edges_out(sym, internal_only=True):
            key = (e["src_symbol"], e["dst_symbol"] or "", e["kind"])
            if key not in seen_edges:
                seen_edges.add(key)
                edges.append({"from": e["src_symbol"], "to": e["dst_symbol"], "kind": e["kind"]})
        for e in store.edges_in(sym, internal_only=True):
            key = (e["src_symbol"], e["dst_symbol"] or "", e["kind"])
            if key not in seen_edges:
                seen_edges.add(key)
                edges.append({"from": e["src_symbol"], "to": e["dst_symbol"], "kind": e["kind"]})

    # Temporal: recent events touching seed features
    recent: list[dict] = []
    for ev in store.recent_events(limit=10):
        fid = ev.op.feature_id or ""
        if fid in seed_features:
            recent.append({"kind": ev.op.kind.value, "feature_id": fid, "at": ev.at.to_str()})

    return {"edges": edges[:20], "recent": recent[:5]}
