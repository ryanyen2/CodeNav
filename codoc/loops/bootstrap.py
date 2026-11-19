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
after the file, with no description. A file's ``__module__`` chunk always goes
to its first top-level node.
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field

from codoc.calculus.derive import description, title
from codoc.calculus.ids import LOOP, new_batch_id
from codoc.doclang import char_budget, norm_key
from codoc.index.flows import flow_lines
from codoc.index.git import commit_rationales
from codoc.index.graph import Graph
from codoc.llm.passes import propose_brief, propose_file_features_raw, propose_organization_raw
from codoc.loops.context import aliases, owner_of
from codoc.loops.edits import Compiler, symbol, title_from_file
from codoc.workspace import Workspace

SOURCE_CAP = 1200
CALLS_CAP = 6
ORG_FEATURE_CAP = 400
COUPLING_CAP = 40
README_CAP = 12_000
META_CAP = 4_000        # pyproject.toml, package.json
HEADER_CAP = 1_200


@dataclass
class BootstrapResult:
    chunks: int = 0
    features: int = 0
    calls: int = 0
    skipped: list[str] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)   # (edit kind, why) an edit that did not land
    unplaced: list[str] = field(default_factory=list)             # top-level features the organizing call did not place

    def summary(self) -> str:
        base = f"{self.chunks} chunks → {self.features} features ({self.calls} LLM calls)"
        base += f", {len(self.skipped)} file(s) skipped" if self.skipped else ""
        base += f", {len(self.unplaced)} feature(s) left unorganized" if self.unplaced else ""
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
        except Exception as exc:  # noqa: BLE001 (a bootstrap without a brief still works)
            say(f"  ⚠ could not read the project's own prose ({exc})")
    titles: list[str] = [title(ws.store, h.id) for h in ws.store.headings()]
    step = max(1, len(files) // 40)
    failures: list[tuple[str, Exception | None]] = []
    for wave_start in range(0, len(files), max(1, concurrency)):
        wave = files[wave_start:wave_start + max(1, concurrency)]
        snapshot = list(titles)
        prepared = []
        for file in wave:
            frows = sorted(by_file[file], key=lambda r: r.symbol)
            chunks = [{"symbol": r.symbol, "source": (r.source or "")[:SOURCE_CAP]} for r in frows]
            edges = _file_edges(frows, graph)
            why = commit_rationales(root_dir, [file], rescale=char_budget) if root_dir else []
            prepared.append((file, frows, chunks, edges, why))

        def call(item):
            file, _rows, chunks, edges, why = item
            last = None
            for _ in (1, 2):
                try:
                    return propose_file(file, chunks, edges, snapshot, repo_name=repo_name, config=config,
                                        why=why, brief=brief, doc_language=doc_language)
                except Exception as exc:  # noqa: BLE001 (per-file tolerance)
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
        alias = aliases(ws.store)
        feats = [{"id": alias[h.id], "title": title(ws.store, h.id), "description": description(ws.store, h.id)}
                 for h in top]
        exported = {b.symbol for b in ws.store.bindings.values() if b.file.endswith("__init__.py")}
        try:
            ops = propose_org(feats, _feature_coupling(ws.store, graph, alias), repo_name=repo_name, config=config,
                              brief=brief, flows=flow_lines(graph, exported), doc_language=doc_language)
            res.calls += 1
            placed = _land_org(ws, res, ops, batch, {a: fid for fid, a in alias.items()})
            res.unplaced = [title(ws.store, h.id) for h in top if h.id not in placed]
            if res.unplaced:
                say(f"  ⚠ the organization pass placed no move for {len(res.unplaced)} feature(s), which stay "
                    "above every theme: " + ", ".join(res.unplaced))
        except Exception as exc:  # noqa: BLE001 (a flat tree is still usable)
            say(f"  ⚠ organization pass failed ({exc}); the tree stays flat")
    elif organize and len(top) > ORG_FEATURE_CAP:
        say(f"  ⚠ {len(top)} top-level features, so the organization pass is skipped")
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
    hashes = {r.symbol: (r.fingerprint, r.types_hash) for r in frows}
    compiler = Compiler(ws.store, hashes=hashes, bindable=set(hashes))
    return [o["title"].strip() for o in ops
            if o.get("kind") == "add_node" and _land(ws, res, compiler, o, batch)]


def _land_org(ws: Workspace, res: BootstrapResult, ops: list[dict], batch: str, ids: dict[str, str]) -> set[str]:
    """The top level in the order the reply gives it. ``ids`` maps the aliases
    the reply names features by to their ids. Returns the features it moved.

    - An ``add_node`` puts a theme at the end of the document.
    - A ``move_node`` puts a feature under its theme or, with no ``parent_id``,
      at the end. A move to a theme the reply adds later waits for it.
    - A theme titled like an existing feature is refused (``bootstrap_org.txt``
      rule 7), and the moves to it with it, so those features stay where they are.
    """
    compiler = Compiler(ws.store)
    placed: set[str] = set()

    def move(o: dict) -> None:
        if fid := _land(ws, res, compiler, {**o, "feature_id": ids.get(o.get("feature_id"), o.get("feature_id"))}, batch):
            placed.add(fid)

    taken = {norm_key(title(ws.store, h.id)) for h in ws.store.headings()}
    themes = {o.get("id") for o in ops if o.get("kind") == "add_node" and o.get("id")}
    added: set[str] = set()
    waiting: dict[str, list[dict]] = {}
    for o in ops:
        if o.get("kind") == "add_node":
            if norm_key(o.get("title")) in taken:
                res.refused.append(("add_node", "a theme titled like a feature"))
                continue
            # A theme sits at the top and owns no code.
            if _land(ws, res, compiler, {**o, "parent_id": None, "bindings": []}, batch) and o.get("id"):
                added.add(o["id"])
                for m in waiting.pop(o["id"], []):
                    move(m)
        elif o.get("kind") == "move_node":
            parent = o.get("parent_id")
            if parent in themes and parent not in added:
                waiting.setdefault(parent, []).append(o)
                continue
            move(o)
    return placed


# ── context helpers ──────────────────────────────────────────────────────────

def _file_edges(frows: list, graph: Graph) -> list[dict]:
    out = []
    for r in frows:
        outs, ins = graph.edges_out(r.symbol), graph.edges_in(r.symbol)
        calls = [e["dst_symbol"] for e in outs if e["kind"] == "call" and e.get("dst_symbol")][:CALLS_CAP]
        called_by = [e["src_symbol"] for e in ins if e["kind"] == "call"][:CALLS_CAP]
        contained = next((e["dst_symbol"] for e in outs if e["kind"] == "contain"), None)
        if calls or called_by or contained:
            entry = {"symbol": r.symbol}
            if calls:
                entry["calls"] = calls
            if called_by:
                entry["called_by"] = called_by
            if contained:
                entry["contained_in"] = contained
            out.append(entry)
    return out


def _ensure_file_coverage(ops: list[dict], frows: list, file: str) -> list[dict]:
    """Every chunk of the file bound by exactly one node.

    - A binding outside the file is dropped. A chunk two nodes bind goes to
      the deeper one, the more specific description of it.
    - What no node bound goes to the node with the most bindings, or to a new
      node named after the file.
    - ``__module__`` goes to the first top-level node.
    - A node left owning nothing, with no children, is dropped: it described
      code another node holds, or only imports (``bootstrap_file.txt`` rule 3)."""
    keys = {r.symbol for r in frows}
    parent = {o.get("id"): o.get("parent_id") for o in ops if o.get("id")}

    def depth(o: dict) -> int:
        d, p = 0, o.get("parent_id")
        while p in parent and d <= len(parent):
            d, p = d + 1, parent[p]
        return d

    owner: dict[str, int] = {}
    for o in sorted(ops, key=depth, reverse=True):     # stable: the reply's order breaks a tie
        for b in o.get("bindings") or []:
            if (key := symbol(b)) in keys:
                owner.setdefault(key, id(o))
    for o in ops:
        o["bindings"] = [k for k in dict.fromkeys(symbol(b) for b in o.get("bindings") or []) if owner.get(k) == id(o)]
    uncovered = sorted(keys - owner.keys())
    adds = [o for o in ops if o.get("kind") == "add_node" and (o.get("title") or "").strip()]
    if uncovered and not adds:
        return list(ops) + [{"kind": "add_node", "title": title_from_file(file), "description": "",
                             "bindings": uncovered, "rationale": "no node for this file"}]
    if uncovered:
        max(adds, key=lambda o: len(o["bindings"]))["bindings"].extend(uncovered)
    roots = [o for o in adds if not o.get("parent_id")]
    holder = next((o for o in adds if any(s.endswith("::__module__") for s in o["bindings"])), None)
    if roots and holder is not None and holder is not roots[0]:
        module = next(s for s in holder["bindings"] if s.endswith("::__module__"))
        holder["bindings"].remove(module)
        roots[0]["bindings"].append(module)
    while True:
        parents = {o.get("parent_id") for o in ops} - {None}
        empty = {id(o) for o in ops if o.get("kind") == "add_node" and not o.get("bindings") and o.get("id") not in parents}
        if not empty:
            return ops
        ops = [o for o in ops if id(o) not in empty]


def _feature_coupling(store, graph: Graph, alias: dict[str, str]) -> list[str]:
    pair: Counter = Counter()
    for e in graph.all_edges():
        if e["kind"] not in ("call", "import") or not e.get("dst_symbol"):
            continue
        a, b = owner_of(store, e["src_symbol"]), owner_of(store, e["dst_symbol"])
        if a and b and a != b:
            pair[(a, b)] += 1
    return [f"{alias[a]} ({title(store, a)}) → {alias[b]} ({title(store, b)}): {n}"
            for (a, b), n in pair.most_common(COUPLING_CAP)]


def _project_prose(root_dir: str | None, rows: list) -> tuple[str, list[dict]]:
    """The README and package metadata, and each file's opening: what precedes
    its first definition (the module docstring, leading comments, imports).
    The index knows where each definition starts, in every language it parses."""
    readme = ""
    try:
        names = {n.casefold(): n for n in os.listdir(root_dir)} if root_dir else {}
    except OSError:
        names = {}
    first_readme = next((names[n] for n in ("readme.md", "readme.rst", "readme.txt", "readme") if n in names), None)
    for name, cap in ((first_readme, README_CAP), (names.get("pyproject.toml"), META_CAP),
                      (names.get("package.json"), META_CAP)):
        if name is None:
            continue
        try:
            with open(os.path.join(root_dir, name), encoding="utf-8", errors="replace") as fh:
                readme += f"\n\n--- {name} ---\n{fh.read(cap)}"
        except OSError:
            continue
    first_def: dict[str, int] = {}
    for r in rows:
        if not r.symbol.endswith("::__module__"):
            first_def[r.file] = min(r.start_byte, first_def.get(r.file, r.start_byte))
    most = HEADER_CAP * 4          # bytes: a UTF-8 character is at most four
    headers: list[dict] = []
    for file in dict.fromkeys(r.file for r in rows) if root_dir else ():
        try:
            with open(os.path.join(root_dir, file), "rb") as fh:
                head = fh.read(min(first_def.get(file, most), most))
        except OSError:
            continue
        opening = head.decode("utf-8", errors="replace").strip()[:HEADER_CAP]
        if opening:
            headers.append({"file": file, "opening": opening})
    return readme.strip(), headers
