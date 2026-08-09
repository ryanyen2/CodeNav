"""What the package offers, and how work flows through it.

A feature tree built file by file and then grouped by coupling counts is a
*taxonomy*: it says what exists and what sits near what. That is not how a
programmer builds an understanding of an unfamiliar codebase. They pick an entry
point — the function a user actually calls — and follow it inward, and the thing
they end up holding is a *path*: a request enters here, is prepared there, goes
out through that, and comes back around. Read a good hand-written overview of any
library and the passage that carries the most understanding per line is almost
always that traversal.

codoc could not write that passage. It had the call graph the whole time — the
``code_edges`` table, and a ``graph.query.entry_points`` that had never been
called from the pipeline — but it only ever asked the graph local questions:
which symbols sit near this one, how strongly do these two features couple. Those
answers shape a taxonomy. Nothing asked the graph the global question, so
nothing in the bootstrap context described a flow, and no prompt however well
written can produce a traversal from an inventory.

This module asks it: where does work enter, and where does it go. The answer
goes into the organization pass, whose job is deciding what the top level of the
tree looks like — so themes can follow the path a request takes rather than
grouping whatever calls whatever most often.

Pure graph work over one in-memory adjacency scan; no LLM, no IO beyond the
store.
"""
from __future__ import annotations

from collections import Counter

from codoc.store.db import Store

_MAX_DEPTH = 7        # past this a path stops being a story and becomes a stack trace
_MAX_FLOWS = 8        # a top-level pass can hold this many narratives, not thirty
_MIN_PATH = 3         # two symbols is an edge, not a flow


def _adjacency(store: Store) -> tuple[dict[str, list[str]], Counter, set[str]]:
    """One scan → (callee lists, in-degree, every symbol with a call edge).

    Built once rather than queried per node: the walk below visits a symbol
    repeatedly, and a store round trip per visit made the whole idea look too
    expensive to run at bootstrap, which is the only time it matters.
    """
    out: dict[str, list[str]] = {}
    indeg: Counter = Counter()
    seen: set[str] = set()
    for e in store.all_edges(internal_only=True):
        if e["kind"] != "call":
            continue
        src, dst = e["src_symbol"], e["dst_symbol"]
        if not src or not dst:
            continue
        out.setdefault(src, []).append(dst)
        indeg[dst] += 1
        seen.add(src)
        seen.add(dst)
    return out, indeg, seen


def _is_public(symbol: str) -> bool:
    """A symbol a caller outside the package could plausibly reach.

    Dunders are the exception worth keeping: ``__init__`` and ``__call__`` are
    reached constantly from outside, and dropping them would cut most flows off
    at their first step.
    """
    leaf = symbol.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
    if leaf == "__module__":
        return False   # the file's top level, not something anyone calls
    return not leaf.startswith("_") or (leaf.startswith("__") and leaf.endswith("__"))


def _files_touched(path: list[str]) -> tuple[str, ...]:
    """The file sequence a path visits, consecutive repeats collapsed."""
    out: list[str] = []
    for sym in path:
        file = sym.split("::", 1)[0]
        if not out or out[-1] != file:
            out.append(file)
    return tuple(out)


def entry_symbols(store: Store) -> list[str]:
    """Public symbols nothing inside the package calls — where work comes in.

    Two sources, because either alone is wrong. Call in-degree zero finds the
    real entry points but also every dead helper; membership in ``__init__.py``
    finds the declared API but misses the entry points a package re-exports
    without defining. Their union, filtered to public names, is close enough to
    "what a user of this package touches first".
    """
    out, indeg, seen = _adjacency(store)
    unreached = {s for s in seen if indeg[s] == 0 and s in out}
    exported = {b.symbol_path for b in store.all_bindings()
                if b.file.endswith("__init__.py")}
    return sorted(s for s in (unreached | exported) if _is_public(s))


def dominant_path(store: Store, entry: str, *, max_depth: int = _MAX_DEPTH,
                  adjacency=None) -> list[str]:
    """Follow the call graph inward from ``entry``, one step at a time.

    At each step it takes the callee that itself calls the most — the branch
    that keeps going. Following the *first* callee instead would wander into
    whichever validation helper happened to sort first; following the
    busiest-onward one traces the spine of the operation, which is the part a
    reader needs.
    """
    out, _indeg, _seen = adjacency or _adjacency(store)
    path = [entry]
    visited = {entry}
    current = entry
    for _ in range(max_depth - 1):
        nexts = [d for d in dict.fromkeys(out.get(current, [])) if d not in visited]
        if not nexts:
            break
        current = max(sorted(nexts), key=lambda s: len(out.get(s, [])))
        visited.add(current)
        path.append(current)
    return path


def flows(store: Store, *, limit: int = _MAX_FLOWS) -> list[list[str]]:
    """The package's main paths — the ones that cross modules — longest first.

    Ranked by how many files a path visits, not how many symbols. A chain of
    seven calls inside one file is that file's internals; the reader who needs
    a map is asking how the *pieces* fit, and every module boundary a path
    crosses is one of those joins made concrete. Sorting by length alone buries
    the request lifecycle under whichever class calls itself the most.

    Deduplicated on the file sequence for the same reason ``get`` and ``post``
    do not both need telling: two paths through the same modules in the same
    order are one story with different first words.
    """
    adjacency = _adjacency(store)
    candidates = [dominant_path(store, e, adjacency=adjacency)
                  for e in entry_symbols(store)]
    scored = [(p, _files_touched(p)) for p in candidates if len(p) >= _MIN_PATH]
    scored = [(p, files) for p, files in scored if len(files) >= 2]
    scored.sort(key=lambda item: (-len(item[1]), -len(item[0]), item[0][0]))

    chosen: list[list[str]] = []
    seen_shapes: set[tuple[str, ...]] = set()
    for path, files in scored:
        if files in seen_shapes:
            continue
        seen_shapes.add(files)
        chosen.append(path)
        if len(chosen) >= limit:
            break
    return chosen


def flow_lines(store: Store, *, limit: int = _MAX_FLOWS) -> list[str]:
    """The flows as prompt lines, one arrow chain each."""
    return [" → ".join(p) for p in flows(store, limit=limit)]
