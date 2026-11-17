"""First run: build the tree from the code.

Three LLM passes:

1. a brief from the project's own prose (README, package metadata, file openings);
2. one call per source file, returning that file's features as ``add_node`` edits;
3. one organizing call that adds theme nodes and moves top-level features under them.

The edits are compiled by ``loops.edits`` and land on the trunk with
``actor=loop``: a first tree has nothing to be a suggestion against. The whole
run is one batch, and a person cannot undo it.

Every chunk ends up bound. What the model did not place goes to the file's
largest new node; a file the model returned nothing for gets one node named
after the file, with no description.
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field

from codoc.calculus.derive import LOOP, description, title
from codoc.calculus.ids import new_batch_id
from codoc.doclang import char_budget
from codoc.index.flows import flow_lines
from codoc.index.git import commit_rationales
from codoc.index.graph import Graph
from codoc.llm.passes import propose_brief, propose_file_features_raw, propose_organization_raw
from codoc.loops.context import owner_of
from codoc.loops.edits import Compiler, symbol, title_from_file
from codoc.workspace import Workspace

SOURCE_CAP = 1200
CALLS_CAP = 6
ORG_FEATURE_CAP = 400
COUPLING_CAP = 40
README_CAP = 12_000
HEADER_CAP = 1_200


@dataclass
class BootstrapResult:
    chunks: int = 0
    features: int = 0
    calls: int = 0
    skipped: list[str] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)   # (edit kind, why) an edit that did not land

    def summary(self) -> str:
        base = f"{self.chunks} chunks → {self.features} features ({self.calls} LLM calls)"
        base += f", {len(self.skipped)} file(s) skipped" if self.skipped else ""
        return base + (f", {len(self.refused)} block(s) refused" if self.refused else "")


def bootstrap(ws: Workspace, rows: list, graph: Graph, *, repo_name: str = "codebase", root_dir: str | None = None,
              config=None, doc_language=None, organize: bool = True, printer=None,
              propose_file=propose_file_features_raw, propose_org=propose_organization_raw,
              propose_the_brief=propose_brief, concurrency: int = 8) -> BootstrapResult:
    say = printer or (lambda *_a, **_k: None)
    res = BootstrapResult(chunks=len(rows))
    batch = new_batch_id()
    by_file: dict[str, list] = {}
    for r in rows:
        by_file.setdefault(r.file, []).append(r)
    files = sorted(by_file)
    brief: dict = {}
    readme, headers = _project_prose(root_dir, rows)
    if readme or headers:
        try:
            brief = propose_the_brief(readme, headers, repo_name=repo_name, config=config, doc_language=doc_language) or {}
            res.calls += 1
        except Exception as exc:  # noqa: BLE001 — a bootstrap without a brief still works
            say(f"  ⚠ could not read the project's own prose ({exc})")
    titles: list[str] = [title(ws.store, h.id) for h in ws.store.headings()]
    step = max(1, len(files) // 40)
    failures: list[tuple[str, Exception | None]] = []
    for wave_start in range(0, len(files), max(1, concurrency)):
        wave = files[wave_start:wave_start + max(1, concurrency)]
        snapshot = list(titles)
        prepared = []
        for file in wave:
            frows = sorted(by_file[file], key=lambda r: r.symbol_path)
            chunks = [{"symbol_path": r.symbol_path, "source": (r.source or "")[:SOURCE_CAP]} for r in frows]
            edges = _file_edges(frows, graph)
            why = commit_rationales(root_dir, [file], cap=char_budget) if root_dir else []
            prepared.append((file, frows, chunks, edges, why))

        def call(item):
            file, _rows, chunks, edges, why = item
            last = None
            for _ in (1, 2):
                try:
                    return propose_file(file, chunks, edges, snapshot, repo_name=repo_name, config=config,
                                        why=why, brief=brief, doc_language=doc_language)
                except Exception as exc:  # noqa: BLE001 — per-file tolerance
                    last = exc
            failures.append((file, last))
            return []

        if concurrency > 1 and len(prepared) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="codoc-bootstrap") as ex:
                results = list(ex.map(call, prepared))
        else:
            results = [call(item) for item in prepared]
        for i, ((file, frows, _c, _e, _w), ops) in enumerate(zip(prepared, results)):
            idx = wave_start + i + 1
            if idx == 1 or idx == len(files) or idx % step == 0:
                say(f"  · [{idx}/{len(files)}] {file}")
            res.calls += 1
            ops = _ensure_file_coverage(ops, frows, file)
            titles.extend(_land_file(ws, res, ops, frows, batch))
    if failures and len(failures) == len(files):
        raise RuntimeError(f"every bootstrap call failed ({len(files)}/{len(files)}); last error: {failures[-1][1]}")
    if failures:
        say(f"  ⚠ {len(failures)} of {len(files)} files could not be described; each is in the tree as a node "
            "named after its file with no description")
    res.skipped = [f for f, _ in failures]

    top = [h for h in ws.store.headings() if h.level == 1]
    if organize and 1 < len(top) <= ORG_FEATURE_CAP:
        feats = [{"id": h.id, "title": title(ws.store, h.id), "description": description(ws.store, h.id)} for h in top]
        exported = {b.symbol for b in ws.store.bindings.values() if b.file.endswith("__init__.py")}
        try:
            ops = propose_org(feats, _feature_coupling(ws.store, graph), repo_name=repo_name, config=config,
                              brief=brief, flows=flow_lines(graph, exported), doc_language=doc_language)
            res.calls += 1
            _land_org(ws, res, ops, batch)
        except Exception as exc:  # noqa: BLE001 — a flat tree is still usable
            say(f"  ⚠ organization pass failed ({exc}); the tree stays flat")
    elif organize and len(top) > ORG_FEATURE_CAP:
        say(f"  ⚠ {len(top)} top-level features — skipping the organization pass")
    res.features = len(ws.store.headings())
    return res


# ── landing the model's edits ────────────────────────────────────────────────

def _land(ws: Workspace, res: BootstrapResult, compiler: Compiler, raw: dict, batch: str) -> str | None:
    """Compile one edit and apply it on the trunk. Returns the feature it made
    or moved, or None when it was refused."""
    change = compiler.compile(raw)
    if change.refused is None:
        conflicts = [o.conflict for o in ws.submit(change.bodies, actor=LOOP, batch=batch) if o.conflict]
        change.refused = conflicts[0] if conflicts else None
    if change.refused:
        res.refused.append((change.kind, change.refused))
        return None
    return change.target


def _land_file(ws: Workspace, res: BootstrapResult, ops: list[dict], frows: list, batch: str) -> list[str]:
    """One file's ``add_node`` edits. Returns the titles added."""
    hashes = {(r.file, r.symbol_path): (r.tokens_hash, r.types_hash) for r in frows}
    compiler = Compiler(ws.store, hashes=hashes, bindable=set(hashes))
    return [o["title"].strip() for o in ops
            if o.get("kind") == "add_node" and _land(ws, res, compiler, o, batch)]


def _land_org(ws: Workspace, res: BootstrapResult, ops: list[dict], batch: str) -> None:
    """Theme nodes first, then the moves that put features under them."""
    compiler = Compiler(ws.store)
    for o in sorted((o for o in ops if o.get("kind") in ("add_node", "move_node")),
                    key=lambda o: o["kind"] != "add_node"):
        if o["kind"] == "add_node":
            o = {**o, "parent_id": None, "bindings": []}       # themes sit at the top and own no code
        _land(ws, res, compiler, o, batch)


# ── context helpers ──────────────────────────────────────────────────────────

def _file_edges(frows: list, graph: Graph) -> list[dict]:
    out = []
    for r in frows:
        outs, ins = graph.edges_out(r.symbol_path), graph.edges_in(r.symbol_path)
        calls = [e["dst_symbol"] for e in outs if e["kind"] == "call" and e.get("dst_symbol")][:CALLS_CAP]
        called_by = [e["src_symbol"] for e in ins if e["kind"] == "call"][:CALLS_CAP]
        contained = next((e["dst_symbol"] for e in outs if e["kind"] == "contain"), None)
        if calls or called_by or contained:
            entry = {"symbol": r.symbol_path}
            if calls:
                entry["calls"] = calls
            if called_by:
                entry["called_by"] = called_by
            if contained:
                entry["contained_in"] = contained
            out.append(entry)
    return out


def _ensure_file_coverage(ops: list[dict], frows: list, file: str) -> list[dict]:
    """Every chunk of the file bound by exactly one node: bindings outside the
    file or bound twice are dropped, and what no node bound goes to the node
    with the most bindings (or to a new node named after the file)."""
    keys = {(r.file, r.symbol_path) for r in frows}
    seen: set[tuple[str, str]] = set()
    for o in ops:
        kept = []
        for b in o.get("bindings") or []:
            key = symbol(b)
            if key in keys and key not in seen:
                seen.add(key)
                kept.append(key[1])
        o["bindings"] = kept
    uncovered = [s for _, s in sorted(keys - seen)]
    if not uncovered:
        return ops
    adds = [o for o in ops if o.get("kind") == "add_node" and (o.get("title") or "").strip()]
    if adds:
        max(adds, key=lambda o: len(o["bindings"]))["bindings"].extend(uncovered)
        return ops
    return list(ops) + [{"kind": "add_node", "title": title_from_file(file), "description": "",
                         "bindings": uncovered, "rationale": "no node for this file"}]


def _feature_coupling(store, graph: Graph) -> list[str]:
    pair: Counter = Counter()
    for e in graph.all_edges():
        if e["kind"] not in ("call", "import") or not e.get("dst_symbol"):
            continue
        a, b = owner_of(store, e["src_symbol"]), owner_of(store, e["dst_symbol"])
        if a and b and a != b:
            pair[(a, b)] += 1
    return [f"{a} ({title(store, a)}) → {b} ({title(store, b)}): {n}" for (a, b), n in pair.most_common(COUPLING_CAP)]


def _project_prose(root_dir: str | None, rows: list) -> tuple[str, list[dict]]:
    import re
    readme = ""
    if root_dir:
        for name in ("README.md", "README.rst", "README.txt", "README", "pyproject.toml", "package.json"):
            path = os.path.join(root_dir, name)
            if os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        readme += f"\n\n--- {name} ---\n{fh.read()[:README_CAP]}"
                except OSError:
                    continue
            if len(readme) > README_CAP:
                break
    headers: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if row.file in seen or not root_dir:
            continue
        seen.add(row.file)
        try:
            with open(os.path.join(root_dir, row.file), encoding="utf-8", errors="replace") as fh:
                text = fh.read(HEADER_CAP * 3)
        except OSError:
            continue
        cut = re.search(r"^(?:def |class |async def )", text, re.M)
        opening = (text[: cut.start()] if cut else text).strip()[:HEADER_CAP]
        if opening:
            headers.append({"file": row.file, "opening": opening})
    return readme.strip(), headers
