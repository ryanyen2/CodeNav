"""Relevance selection for an LLM pass or an agent read (was ``loop/subtree.py``).

Seeds are the features bound in the touched files (plus any changed symbols),
expanded one hop along the code graph, plus each feature's parent and
children. Features with queued work and no bindings (planned) are always in
scope, so a fresh chunk that fulfils an accepted plan is attached to it rather
than duplicated beside it.

Every feature entry is block-keyed: its description is a list of
``{id, text}`` paragraphs, which is what lets the model's amend name the
paragraph it changes (spec §4, rule 2).
"""
from __future__ import annotations

from codoc.calculus.derive import markdown, phase
from codoc.calculus.doc import PARAGRAPH, parent_of
from codoc.calculus.store import Store
from codoc.graph.graph import Graph


def owner_of(store: Store, symbol: str) -> str | None:
    if "::" not in symbol:
        return None
    file = symbol.split("::", 1)[0]
    b = store.bindings.get((file, symbol))
    return b.feature if b else None


def written_by(store: Store, fid: str) -> str:
    """Who last wrote this feature's prose: the actor of its newest live
    segment across title and body. ``human`` for any person."""
    newest = ("", "")
    for b in store.feature_body(fid):
        for s in b.text().segs:
            if s.live and s.at > newest[0]:
                newest = (s.at, s.actor)
    actor = newest[1]
    return "human" if actor.startswith("human:") else (actor or "")


def feature_entry(store: Store, fid: str, *, full: bool = True) -> dict:
    headings = store.headings()
    idx = next(i for i, h in enumerate(headings) if h.id == fid)
    entry = {
        "id": fid,
        "title": markdown(store.blocks[fid].text(), "rejected", store),
        "parent_id": parent_of(headings, idx),
    }
    if full:
        entry["blocks"] = [{"id": b.id, "text": markdown(b.text(), "rejected", store)}
                           for b in store.feature_body(fid)[1:] if b.kind == PARAGRAPH]
        entry["bindings"] = sorted(b.symbol for b in store.bindings.values() if b.feature == fid)
        wb = written_by(store, fid)
        if wb:
            entry["written_by"] = wb
    if phase(store, fid) == "planned":
        entry["planned"] = True
    return entry


def select_context(store: Store, graph: Graph, files: set[str], changed_symbols: set[str] | None = None,
                   *, hops: int = 1, recent_ops: int = 5) -> tuple[list[dict], list[dict], dict]:
    """``(subtree, all_titles, context)`` as JSON-ready dicts."""
    changed_symbols = set(changed_symbols or ())
    seed_bindings = [b for b in store.bindings.values() if b.file in files]
    seed_features = {b.feature for b in seed_bindings}
    seed_symbols = {b.symbol for b in seed_bindings}
    related = set(seed_features)
    for sym in graph.ego(seed_symbols | changed_symbols, hops=hops):
        f = owner_of(store, sym)
        if f:
            related.add(f)
    headings = store.headings()
    index = {h.id: i for i, h in enumerate(headings)}
    ids = set(related)
    for fid in list(related):
        if fid not in index:
            continue
        p = parent_of(headings, index[fid])
        if p:
            ids.add(p)
        # children: headings dominated by fid
        for b in store.feature_range(fid)[1:]:
            if b.kind == "heading" and parent_of(headings, index[b.id]) == fid:
                ids.add(b.id)
    for h in headings:
        if phase(store, h.id) == "planned":
            ids.add(h.id)
    subtree = [feature_entry(store, fid) for fid in sorted(ids) if fid in index]
    all_titles = [feature_entry(store, h.id, full=False) for h in headings]
    context = _graph_context(store, graph, changed_symbols or seed_symbols, seed_features, recent_ops)
    return subtree, all_titles, context


def _graph_context(store: Store, graph: Graph, symbols: set[str], seed_features: set[str], recent_ops: int) -> dict:
    edges: list[dict] = []
    seen: set[tuple] = set()
    for sym in sorted(symbols):
        for e in graph.edges_out(sym) + graph.edges_in(sym):
            k = (e["src_symbol"], e.get("dst_symbol") or "", e["kind"])
            if k not in seen:
                seen.add(k)
                edges.append({"from": e["src_symbol"], "to": e.get("dst_symbol"), "kind": e["kind"]})
    recent: list[dict] = []
    for op in reversed(store.ops):
        if len(recent) >= recent_ops:
            break
        fid = op.target.get("fid") or store.feature_of(op.target.get("bid", "")) if op.target else None
        if fid in seed_features and op.conflict is None:
            recent.append({"kind": op.kind, "feature_id": fid, "actor": op.actor, "at": op.at})
    return {"edges": edges[:20], "recent": recent}


def impacted_features(store: Store, graph: Graph, changed_symbols: set[str]) -> dict[str, list[str]]:
    """Features whose code calls or imports the changed symbols (upstream
    dependents), with the symbols that make them so."""
    out: dict[str, list[str]] = {}
    changed_owner = {s: owner_of(store, s) for s in changed_symbols}
    for sym in changed_symbols:
        for caller in graph.neighbors(sym, kinds={"call", "import", "type_ref", "inherit"}, direction="in"):
            f = owner_of(store, caller)
            if f and f != changed_owner.get(sym):
                out.setdefault(f, []).append(sym)
    return out
