"""High-level graph queries over the code_edges table."""
from __future__ import annotations

from collections import defaultdict, deque

from codoc.graph.extract import extract_edges, extract_edges_for_changed
from codoc.pipelines.indexing.reader import ChunkRow
from codoc.store.db import Store


def build_graph(store: Store, rows: list[ChunkRow]) -> None:
    """Full rebuild: drop all edges and re-extract from rows."""
    edges = extract_edges(rows)
    store.drop_all_edges()
    store.insert_edges(edges)


def update_graph(store: Store, rows: list[ChunkRow], changed_files: set[str]) -> None:
    """Incremental: re-extract only changed_files edges, resolve against full symbol table."""
    if not changed_files:
        return
    store.delete_edges_from_files(changed_files)
    changed_rows = [r for r in rows if r.file in changed_files]
    edges = extract_edges_for_changed(changed_rows, rows)
    store.insert_edges(edges)


def neighbors(
    store: Store,
    symbol: str,
    kinds: set[str] | None = None,
    direction: str = "out",
) -> list[str]:
    """Return adjacent symbol_paths (internal=1 edges only).

    direction: 'out' (symbol → others), 'in' (others → symbol), 'both'
    kinds: filter by edge kind; None = all kinds
    """
    results: list[str] = []

    if direction in {"out", "both"}:
        for e in store.edges_out(symbol, internal_only=True):
            if (kinds is None or e["kind"] in kinds) and e["dst_symbol"]:
                results.append(e["dst_symbol"])

    if direction in {"in", "both"}:
        for e in store.edges_in(symbol, internal_only=True):
            if kinds is None or e["kind"] in kinds:
                results.append(e["src_symbol"])

    return results


def ego_graph(
    store: Store,
    symbols: set[str],
    hops: int = 1,
    kinds: set[str] | None = None,
) -> set[str]:
    """n-hop expansion from seed symbols along internal edges (both directions).

    Returns the set of all reachable symbols, including the seeds.
    """
    visited: set[str] = set(symbols)
    frontier = set(symbols)

    for _ in range(hops):
        next_frontier: set[str] = set()
        for sym in frontier:
            for adj in neighbors(store, sym, kinds=kinds, direction="both"):
                if adj not in visited:
                    visited.add(adj)
                    next_frontier.add(adj)
        frontier = next_frontier
        if not frontier:
            break

    return visited


def topological_order(store: Store) -> list[str]:
    """Kahn's topological sort — callees and contained members appear before callers/containers.

    Edge semantics and direction used for sorting:
      contain (method→class): method is the leaf; keep original direction so method
                              has zero in-degree and appears first.
      call    (foo→bar):      bar is the dependency; reverse so bar has zero in-degree
                              and appears first (HCGS bottom-up: summarise leaves first).

    Cycles are broken by stable symbol ordering; cycle members appended last.
    """
    edges = store.all_edges(internal_only=True)

    fwd_out: dict[str, list[str]] = defaultdict(list)   # contain: src→dst (method→class)
    rev_out: dict[str, list[str]] = defaultdict(list)   # call: dst→src (bar→foo)
    in_degree: dict[str, int] = defaultdict(int)
    all_symbols: set[str] = set()

    for e in edges:
        kind, src, dst = e["kind"], e["src_symbol"], e["dst_symbol"]
        if kind not in {"call", "contain"} or not dst:
            continue
        all_symbols.update((src, dst))
        if kind == "contain":
            # method → class; method should appear first
            fwd_out[src].append(dst)
            in_degree[dst] += 1
            in_degree.setdefault(src, 0)
        else:  # call
            # foo calls bar → bar should appear first; process as reversed edge bar→foo
            rev_out[dst].append(src)
            in_degree[src] += 1
            in_degree.setdefault(dst, 0)

    queue: deque[str] = deque(sorted(s for s in all_symbols if in_degree.get(s, 0) == 0))
    order: list[str] = []

    while queue:
        sym = queue.popleft()
        order.append(sym)
        for nxt in sorted(fwd_out.get(sym, []) + rev_out.get(sym, [])):
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)

    ordered_set = set(order)
    order.extend(sorted(s for s in all_symbols if s not in ordered_set))
    return order


def neighbor_feature(store: Store, symbol: str) -> str | None:
    """Return the feature_id owning the most graph-neighbors of ``symbol``.

    Used as a last-resort home for an added chunk no feature covers: it lands
    with the feature its callers/callees already belong to, rather than being
    dropped or minting a junk node.
    """
    neighbor_syms: list[str] = []
    for e in store.edges_out(symbol, internal_only=True):
        if e["dst_symbol"]:
            neighbor_syms.append(e["dst_symbol"])
    for e in store.edges_in(symbol, internal_only=True):
        neighbor_syms.append(e["src_symbol"])

    feat_count: dict[str, int] = {}
    for sym in neighbor_syms:
        if not sym or "::" not in sym:
            continue
        b = store.binding_at(sym.split("::", 1)[0], sym)
        if b:
            feat_count[b.feature_id] = feat_count.get(b.feature_id, 0) + 1

    return max(feat_count, key=feat_count.get) if feat_count else None


def entry_points(store: Store) -> list[str]:
    """Symbols that call others but are never called (internal call edges only).

    These are likely public API entry points or script entry points.
    """
    edges = store.all_edges(internal_only=True)
    all_src: set[str] = set()
    all_dst: set[str] = set()

    for e in edges:
        if e["kind"] != "call":
            continue
        all_src.add(e["src_symbol"])
        if e["dst_symbol"]:
            all_dst.add(e["dst_symbol"])

    return sorted(all_src - all_dst)


# The kinds of edge that make one feature's behaviour depend on another's. A
# reference that is neither a call, an import, nor an inheritance does not put the
# referring code at risk when the referent changes, so it is not impact.
IMPACT_KINDS = frozenset({"call", "import", "inherit"})
# How many symbols one entry carries as evidence. The list is there to make the
# claim checkable, not to reproduce the graph in the sidecar.
_MAX_VIA = 5


def feature_impact(store: Store, edges: list | None = None) -> dict[str, list[dict]]:
    """Which features would feel a change to each feature — Sillito's group 4.

    ``{feature_id -> [{feature_id, title, via: [symbol_path, ...], count}]}``, one
    entry per DEPENDENT feature, ordered by how many symbols tie it to the subject
    and then by title so the answer is stable between passes.

    This is the question a reader asks *before* editing ("what happens if I change
    this?"), so it is a standing property of the tree and not a by-product of a
    change. ``loop_a._compute_impacted`` answers the neighbouring question — who was
    affected by what just happened — off a changeset, and feeds the LLM pass; the two
    are deliberately separate, because a reader who has changed nothing yet has no
    changeset to compute from.

    It is a DERIVED index, like bindings, and it stays out of the prose. A
    description listing its own dependents would be the inventory-of-machinery defect
    the altitude rule exists to stop, and it would go stale the moment somebody added
    a caller -- which is exactly the class of fact a rebuildable index should hold
    instead of a sentence.

    One sweep over the bindings and one over the internal edges, because per-feature
    queries would be a query per bound symbol and the caller wants the whole tree.
    ``edges`` lets a caller that has already read the edge table hand the rows over,
    which is what the sidecar render does -- it runs on every loop tick and computes
    feature coupling from the same rows.
    """
    owner: dict[str, str] = {}
    for b in store.all_bindings():
        owner.setdefault(b.symbol_path, b.feature_id)
    if not owner:
        return {}
    rows = store.all_edges(internal_only=True) if edges is None else edges

    # subject feature -> dependent feature -> the dependent's symbols doing the depending
    raw: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for e in rows:
        if e["kind"] not in IMPACT_KINDS:
            continue
        src, dst = e["src_symbol"], e["dst_symbol"]
        if not src or not dst:
            continue
        subject, dependent = owner.get(dst), owner.get(src)
        # A feature calling itself is not impact: the reader is already reading it.
        if not subject or not dependent or subject == dependent:
            continue
        raw[subject][dependent].add(src)

    out: dict[str, list[dict]] = {}
    for subject, dependents in raw.items():
        hits = []
        for fid, syms in dependents.items():
            f = store.get_feature(fid)
            if f is None or f.retired:
                continue  # a retired feature cannot be affected by anything
            hits.append({
                "feature_id": fid,
                "title": f.title or fid,
                "count": len(syms),
                "via": sorted(syms)[:_MAX_VIA],
            })
        if hits:
            hits.sort(key=lambda r: (-r["count"], r["title"]))
            out[subject] = hits
    return out
