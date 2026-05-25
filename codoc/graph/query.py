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
