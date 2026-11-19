"""The code dependency graph as an in-memory structure with a JSON cache.

Derived from the index (``extract.py`` reads chunk sources with tree-sitter),
never part of the calculus, safe to drop and rebuild. Edges are the dicts
``extract`` produces: ``{src_file, src_symbol, dst_name, dst_symbol, dst_file,
kind, internal}``. Traversal follows ``internal == 1`` edges only.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from codoc.index.extract import extract_edges, extract_edges_for_changed


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

    def ego(self, symbols: set[str]) -> set[str]:
        """``symbols`` and every symbol one edge away from them, either way."""
        return set(symbols).union(*(self.neighbors(s) for s in symbols))

    def neighbor_feature(self, symbol: str, owner_of) -> str | None:
        """The feature owning most of ``symbol``'s neighbours; ``owner_of(symbol)``
        returns a feature id or None."""
        count: dict[str, int] = {}
        for s in self.neighbors(symbol):
            f = owner_of(s)
            if f:
                count[f] = count.get(f, 0) + 1
        return max(count, key=count.get) if count else None

    # ── cache ──────────────────────────────────────────────────────────────
    def dumps(self) -> str:
        """The graph as JSON text for the caller to write; ``load`` reads it back."""
        return json.dumps(list(self._edges.values()), ensure_ascii=False)

    @classmethod
    def load(cls, path: str | Path) -> "Graph":
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            return cls(json.loads(p.read_text()))
        except (json.JSONDecodeError, TypeError, KeyError):
            return cls()
