"""Where work enters the package and where it goes.

Pure graph work over the in-memory ``Graph``. The organization pass reads
these paths so the top level of the tree can follow the route a request takes
rather than grouping whatever calls whatever most often.
"""
from __future__ import annotations

from collections import Counter

from codoc.index.graph import Graph

MAX_DEPTH = 7
MAX_FLOWS = 8
MIN_PATH = 3


def _adjacency(graph: Graph) -> tuple[dict[str, list[str]], Counter, set[str]]:
    out: dict[str, list[str]] = {}
    indeg: Counter = Counter()
    seen: set[str] = set()
    for e in graph.all_edges():
        if e["kind"] != "call" or not e["src_symbol"] or not e.get("dst_symbol"):
            continue
        out.setdefault(e["src_symbol"], []).append(e["dst_symbol"])
        indeg[e["dst_symbol"]] += 1
        seen.update((e["src_symbol"], e["dst_symbol"]))
    return out, indeg, seen


def _is_public(symbol: str) -> bool:
    leaf = symbol.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
    if leaf == "__module__":
        return False
    return not leaf.startswith("_") or (leaf.startswith("__") and leaf.endswith("__"))


def _files_touched(path: list[str]) -> tuple[str, ...]:
    out: list[str] = []
    for sym in path:
        f = sym.split("::", 1)[0]
        if not out or out[-1] != f:
            out.append(f)
    return tuple(out)


def entry_symbols(graph: Graph, exported: set[str]) -> list[str]:
    """Public symbols nothing inside the package calls, plus what ``__init__``
    files export."""
    out, indeg, seen = _adjacency(graph)
    unreached = {s for s in seen if indeg[s] == 0 and s in out}
    return sorted(s for s in (unreached | exported) if _is_public(s))


def dominant_path(adjacency, entry: str, max_depth: int = MAX_DEPTH) -> list[str]:
    out, _, _ = adjacency
    path, visited, cur = [entry], {entry}, entry
    for _ in range(max_depth - 1):
        nexts = [d for d in dict.fromkeys(out.get(cur, [])) if d not in visited]
        if not nexts:
            break
        cur = max(sorted(nexts), key=lambda s: len(out.get(s, [])))
        visited.add(cur)
        path.append(cur)
    return path


def flows(graph: Graph, exported: set[str], limit: int = MAX_FLOWS) -> list[list[str]]:
    adj = _adjacency(graph)
    cands = [dominant_path(adj, e) for e in entry_symbols(graph, exported)]
    scored = [(p, _files_touched(p)) for p in cands if len(p) >= MIN_PATH]
    scored = [(p, f) for p, f in scored if len(f) >= 2]
    scored.sort(key=lambda it: (-len(it[1]), -len(it[0]), it[0][0]))
    chosen: list[list[str]] = []
    shapes: set[tuple[str, ...]] = set()
    for p, f in scored:
        if f in shapes:
            continue
        shapes.add(f)
        chosen.append(p)
        if len(chosen) >= limit:
            break
    return chosen


def flow_lines(graph: Graph, exported: set[str], limit: int = MAX_FLOWS) -> list[str]:
    return [" → ".join(p) for p in flows(graph, exported, limit)]
