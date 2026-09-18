"""The code dependency graph as an in-memory structure with a JSON cache.

Derived from the index (``extract.py`` reads chunk sources with tree-sitter),
never part of the calculus, safe to drop and rebuild. Edges are the dicts
``extract`` produces: ``{src_file, src_symbol, dst_name, dst_symbol, dst_file,
kind, internal}``. Traversal follows ``internal == 1`` edges only.
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path

from codoc.graph.extract import extract_edges, extract_edges_for_changed


class Graph:
    def __init__(self, edges: list[dict] | None = None) -> None:
        self._edges: dict[tuple[str, str, str], dict] = {}
        self._out: dict[str, list[dict]] = defaultdict(list)
        self._in: dict[str, list[dict]] = defaultdict(list)
        self._by_file: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for e in edges or []:
            self._add(e)

    # ── building ───────────────────────────────────────────────────────────
    def _key(self, e: dict) -> tuple[str, str, str]:
        return (e["src_symbol"], e["dst_name"], e["kind"])

    def _add(self, e: dict) -> None:
        k = self._key(e)
        if k in self._edges:
            self._remove(k)
        self._edges[k] = e
        self._out[e["src_symbol"]].append(e)
        if e.get("dst_symbol"):
            self._in[e["dst_symbol"]].append(e)
        self._by_file[e["src_file"]].append(k)

    def _remove(self, k: tuple[str, str, str]) -> None:
        e = self._edges.pop(k)
        self._out[e["src_symbol"]] = [x for x in self._out[e["src_symbol"]] if self._key(x) != k]
        if e.get("dst_symbol"):
            self._in[e["dst_symbol"]] = [x for x in self._in[e["dst_symbol"]] if self._key(x) != k]
        self._by_file[e["src_file"]] = [x for x in self._by_file[e["src_file"]] if x != k]

    def rebuild(self, rows: list) -> None:
        fresh = Graph(extract_edges(rows))
        self.__dict__.update(fresh.__dict__)

    def update(self, rows: list, changed_files: set[str]) -> None:
        """Re-extract only the changed files' edges, resolved against all rows."""
        if not changed_files:
            return
        for f in changed_files:
            for k in list(self._by_file.get(f, [])):
                if k in self._edges:
                    self._remove(k)
            self._by_file[f] = []
        changed_rows = [r for r in rows if r.file in changed_files]
        for e in extract_edges_for_changed(changed_rows, rows):
            self._add(e)

    # ── queries ────────────────────────────────────────────────────────────
    def edges_out(self, symbol: str) -> list[dict]:
        return [e for e in self._out.get(symbol, []) if e.get("internal")]

    def edges_in(self, symbol: str) -> list[dict]:
        return [e for e in self._in.get(symbol, []) if e.get("internal")]

    def all_edges(self) -> list[dict]:
        return [e for e in self._edges.values() if e.get("internal")]

    def neighbors(self, symbol: str, kinds: set[str] | None = None, direction: str = "both") -> list[str]:
        out: list[str] = []
        if direction in ("out", "both"):
            out.extend(e["dst_symbol"] for e in self.edges_out(symbol)
                       if e.get("dst_symbol") and (kinds is None or e["kind"] in kinds))
        if direction in ("in", "both"):
            out.extend(e["src_symbol"] for e in self.edges_in(symbol) if kinds is None or e["kind"] in kinds)
        return out

    def ego(self, symbols: set[str], hops: int = 1, kinds: set[str] | None = None) -> set[str]:
        visited = set(symbols)
        frontier = set(symbols)
        for _ in range(hops):
            nxt: set[str] = set()
            for s in frontier:
                for a in self.neighbors(s, kinds):
                    if a not in visited:
                        visited.add(a)
                        nxt.add(a)
            frontier = nxt
            if not frontier:
                break
        return visited

    def neighbor_feature(self, symbol: str, owner_of) -> str | None:
        """The feature owning most of ``symbol``'s neighbours; ``owner_of(symbol)``
        returns a feature id or None."""
        count: dict[str, int] = {}
        for s in self.neighbors(symbol):
            f = owner_of(s)
            if f:
                count[f] = count.get(f, 0) + 1
        return max(count, key=count.get) if count else None

    def entry_points(self) -> list[str]:
        src, dst = set(), set()
        for e in self.all_edges():
            if e["kind"] != "call":
                continue
            src.add(e["src_symbol"])
            if e.get("dst_symbol"):
                dst.add(e["dst_symbol"])
        return sorted(src - dst)

    def topological_order(self) -> list[str]:
        fwd: dict[str, list[str]] = defaultdict(list)
        rev: dict[str, list[str]] = defaultdict(list)
        indeg: dict[str, int] = defaultdict(int)
        syms: set[str] = set()
        for e in self.all_edges():
            kind, s, d = e["kind"], e["src_symbol"], e.get("dst_symbol")
            if kind not in ("call", "contain") or not d:
                continue
            syms.update((s, d))
            if kind == "contain":
                fwd[s].append(d)
                indeg[d] += 1
                indeg.setdefault(s, 0)
            else:
                rev[d].append(s)
                indeg[s] += 1
                indeg.setdefault(d, 0)
        q = deque(sorted(s for s in syms if indeg.get(s, 0) == 0))
        order: list[str] = []
        while q:
            s = q.popleft()
            order.append(s)
            for n in sorted(fwd.get(s, []) + rev.get(s, [])):
                indeg[n] -= 1
                if indeg[n] == 0:
                    q.append(n)
        done = set(order)
        order.extend(sorted(s for s in syms if s not in done))
        return order

    # ── cache ──────────────────────────────────────────────────────────────
    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(list(self._edges.values()), ensure_ascii=False))
        tmp.replace(p)

    @classmethod
    def load(cls, path: str | Path) -> "Graph":
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            return cls(json.loads(p.read_text()))
        except (json.JSONDecodeError, TypeError, KeyError):
            return cls()
