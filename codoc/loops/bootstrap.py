"""First run: build the tree from the code.

Three LLM passes: a brief from the project's own
prose, one call per source file proposing that file's features, and one
organizing pass grouping top-level features under themes. Their answers become
ops with ``actor=loop`` on the trunk: a first tree is not a suggestion, there
is nothing for it to be a suggestion against. The whole run is one batch, and
it is the one batch a person cannot undo (spec §3.1).

Every file's chunks end up bound: what the model did not place folds into the
file's largest new node, and a file the model said nothing about gets one node
named after it with no prose, a gap a person can see and fill.
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field

from codoc.calculus import ops as K
from codoc.calculus.derive import LOOP, description, title
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.ids import START, new_batch_id, new_block_id
from codoc.doclang import char_budget
from codoc.index.flows import flow_lines
from codoc.index.git import commit_rationales
from codoc.index.graph import Graph
from codoc.llm.passes import propose_brief, propose_file_features_raw, propose_organization_raw
from codoc.loops.context import owner_of
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
    refused: list[tuple[str, str]] = field(default_factory=list)   # (bid, why) a title or paragraph did not land

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
            minted = _apply_add_ops(ws, res, ops, frows, batch)
            titles.extend(minted)
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
            _apply_org_ops(ws, res, ops, batch)
        except Exception as exc:  # noqa: BLE001 — a flat tree is still usable
            say(f"  ⚠ organization pass failed ({exc}); the tree stays flat")
    elif organize and len(top) > ORG_FEATURE_CAP:
        say(f"  ⚠ {len(top)} top-level features — skipping the organization pass")
    res.features = len(ws.store.headings())
    return res


# ── applying the model's ops ─────────────────────────────────────────────────

def _one_line(md: str) -> str:
    """A heading and a paragraph are each one line: a model title carrying a
    newline, or a hand-wrapped paragraph, is refused ``multi_paragraph`` and
    the tree would keep a block with no words in it at all."""
    return " ".join((md or "").split())


def _add_feature(ws: Workspace, res: BootstrapResult, *, title_md: str, description_md: str, after: str,
                 level: int, batch: str, binds: list[tuple[str, str]], fps: dict, ths: dict) -> str:
    fid = new_block_id()
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": fid}, "payload": {"kind": HEADING, "level": level, "after": after}}],
              actor=LOOP, batch=batch)
    got, _ = ws.ingest(fid, "text", base_text="", new_md=_one_line(title_md) or "Untitled", actor=LOOP, batch=batch)
    if got.refused:
        res.refused.append((fid, f"title: {got.refused}"))
    last = fid
    for para in [_one_line(p) for p in (description_md or "").split("\n\n") if p.strip()]:
        bid = new_block_id()
        ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": bid}, "payload": {"kind": PARAGRAPH, "after": last}}],
                  actor=LOOP, batch=batch)
        got, _ = ws.ingest(bid, "text", base_text="", new_md=para, actor=LOOP, batch=batch)
        if got.refused:
            res.refused.append((bid, got.refused))
        last = bid
    if binds:
        ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": fid},
                    "payload": {"file": f, "symbol": s, "fingerprint": fps.get((f, s), ""), "types_hash": ths.get((f, s), "")}}
                   for f, s in binds], actor=LOOP, batch=batch)
    return fid


def _last_block(ws: Workspace) -> str:
    live = ws.store.ordered()
    return live[-1].id if live else START


def _apply_add_ops(ws: Workspace, res: BootstrapResult, ops: list[dict], frows: list, batch: str) -> list[str]:
    """Apply one file's ``add_node`` ops. Temporary ids (``n1``) resolve to the
    minted heading; a child lands right after its parent's body one level deeper;
    an unknown parent means top level. Returns the titles minted."""
    fps = {(r.file, r.symbol_path): r.tokens_hash for r in frows}
    ths = {(r.file, r.symbol_path): r.types_hash for r in frows}
    local: dict[str, str] = {}
    minted: list[str] = []
    for o in ops:
        if o.get("kind") != "add_node" or not (o.get("title") or "").strip():
            continue
        parent = local.get(o.get("parent_id") or "") or (o.get("parent_id") if o.get("parent_id") in ws.store.blocks else None)
        if parent:
            rng = ws.store.feature_range(parent)
            after, level = rng[-1].id, ws.store.blocks[parent].level + 1
        else:
            after, level = _last_block(ws), 1
        binds = [(str(b[0]), str(b[1])) for b in o.get("bindings", []) if isinstance(b, (list, tuple)) and len(b) == 2]
        fid = _add_feature(ws, res, title_md=o["title"], description_md=o.get("description", ""), after=after,
                           level=level, batch=batch, binds=binds, fps=fps, ths=ths)
        if o.get("id"):
            local[str(o["id"])] = fid
        minted.append(o["title"].strip())
    return minted


def _apply_org_ops(ws: Workspace, res: BootstrapResult, ops: list[dict], batch: str) -> None:
    """Theme headings (``add_node`` with temporary ids) then ``move_node`` of
    existing features under them: one ``block.move`` per feature, deeper by one."""
    local: dict[str, str] = {}
    for o in ops:
        if o.get("kind") == "add_node" and (o.get("title") or "").strip():
            fid = _add_feature(ws, res, title_md=o["title"], description_md=o.get("description", ""),
                               after=_last_block(ws), level=1, batch=batch, binds=[], fps={}, ths={})
            if o.get("id"):
                local[str(o["id"])] = fid
    for o in ops:
        if o.get("kind") != "move_node":
            continue
        fid = o.get("feature_id")
        parent = local.get(o.get("parent_id") or "") or o.get("parent_id")
        if fid not in ws.store.blocks or parent not in ws.store.blocks or fid == parent:
            continue
        rng = ws.store.feature_range(parent)
        if fid in {b.id for b in rng}:
            continue
        delta = (ws.store.blocks[parent].level + 1) - ws.store.blocks[fid].level
        ws.submit([{"kind": K.BLOCK_MOVE, "target": {"bid": fid}, "payload": {"after": rng[-1].id, "level_delta": delta}}],
                  actor=LOOP, batch=batch)


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
    keys = {(r.file, r.symbol_path) for r in frows}
    for o in ops:
        binds = [tuple(b) for b in o.get("bindings", []) if isinstance(b, (list, tuple)) and len(b) == 2]
        o["bindings"] = [list(b) for b in binds if b in keys]
    covered = {tuple(b) for o in ops for b in o.get("bindings", [])}
    uncovered = sorted(keys - covered)
    if not uncovered:
        return ops
    adds = [o for o in ops if o.get("kind") == "add_node" and (o.get("title") or "").strip()]
    if adds:
        primary = max(adds, key=lambda o: len(o.get("bindings", [])))
        primary["bindings"] = list(primary.get("bindings", [])) + [list(b) for b in uncovered]
        return ops
    return list(ops) + [{"kind": "add_node", "title": title_from_file(file), "description": "",
                         "bindings": [list(b) for b in uncovered], "rationale": "coverage: no nodes for this file"}]


def title_from_file(file: str) -> str:
    """A file's name as a feature title: the reflect pass proposes one for a
    file the tree has never named, the same way bootstrap did."""
    stem = os.path.splitext(os.path.basename(file))[0]
    words = stem.replace("-", " ").replace("_", " ").strip()
    return (words[:1].upper() + words[1:]) if words else file


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
