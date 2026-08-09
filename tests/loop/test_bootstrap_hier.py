"""Hierarchical bootstrap — per-file pass + org pass (mocked LLM, no index)."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from codoc.graph.query import build_graph
from codoc.loop.bootstrap_hier import (
    _apply_ops_with_local_ids,
    _ensure_file_coverage,
    _feature_coupling,
    bootstrap_hier_from_chunks,
)
from codoc.model.event import NodeOp, NodeOpKind
from codoc.store.db import open_store


@dataclass
class FakeRow:
    file: str
    symbol_path: str
    source: str = ""
    language: str = "python"
    id: int = 0
    tokens_hash: str = "h"
    types_hash: str = "t"
    start_byte: int = 0
    end_byte: int = 0
    embedding: object = None


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _add(temp_id, title, bindings, parent_id=None):
    return NodeOp(
        kind=NodeOpKind.ADD_NODE,
        feature_id=temp_id,          # bootstrap_agent stashes the local id here
        parent_id=parent_id,
        title=title,
        description=f"{title} does things.",
        bindings=bindings,
    )


# ---------------------------------------------------------------------------
# Local-id resolution + apply
# ---------------------------------------------------------------------------

def test_local_id_nesting_within_one_call(store):
    """A child referencing a sibling's temp id nests under it after apply.

    This is the core fix: within a SINGLE call the LLM nests a new node under
    another new node via temp ids, which the old apply path could not do.
    """
    ops = [
        _add("n1", "Parent", [("a.py", "a.py::Parent")]),
        _add("n2", "Child", [("a.py", "a.py::Parent.child")], parent_id="n1"),
    ]
    _apply_ops_with_local_ids(ops, store, {}, source="bootstrap")

    feats = {f.title: f for f in store.list_features()}
    assert set(feats) == {"Parent", "Child"}
    assert feats["Child"].parent_id == feats["Parent"].id
    assert feats["Parent"].parent_id is None


def test_local_id_hallucinated_parent_falls_to_top_level(store):
    """An add_node whose parent_id resolves to nothing lands at top level (no crash)."""
    ops = [_add("n1", "Orphan", [("a.py", "a.py::x")], parent_id="does-not-exist")]
    _apply_ops_with_local_ids(ops, store, {}, source="bootstrap")
    feats = store.list_features()
    assert len(feats) == 1 and feats[0].parent_id is None


def test_move_node_nests_existing_feature_under_new_theme(store):
    """Org-style ops: a new theme (temp id) adopts an existing real feature."""
    # Seed an existing feature.
    _apply_ops_with_local_ids(
        [_add("n1", "Sessions", [("s.py", "s.py::Session")])], store, {}, source="bootstrap"
    )
    real_id = store.list_features()[0].id

    ops = [
        _add("t1", "HTTP lifecycle", []),
        NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id=real_id, parent_id="t1"),
    ]
    _apply_ops_with_local_ids(ops, store, {}, source="bootstrap")

    theme = next(f for f in store.list_features() if f.title == "HTTP lifecycle")
    sessions = store.get_feature(real_id)
    assert theme.parent_id is None
    assert sessions.parent_id == theme.id


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def test_coverage_folds_uncovered_into_primary_same_file(store):
    """Uncovered chunks fold into the file's largest node — never a new junk node."""
    rows = [
        FakeRow("a.py", "a.py::Big"),
        FakeRow("a.py", "a.py::Big.m1"),
        FakeRow("a.py", "a.py::stray"),  # left uncovered by the model
    ]
    ops = [_add("n1", "Big thing", [("a.py", "a.py::Big"), ("a.py", "a.py::Big.m1")])]
    out = _ensure_file_coverage(ops, rows, "a.py")
    assert len(out) == 1  # no new node minted
    assert ("a.py", "a.py::stray") in out[0].bindings


def test_coverage_mints_one_node_when_model_returns_nothing(store):
    rows = [FakeRow("util.py", "util.py::a"), FakeRow("util.py", "util.py::b")]
    out = _ensure_file_coverage([], rows, "util.py")
    assert len(out) == 1
    assert out[0].kind is NodeOpKind.ADD_NODE
    covered = set(out[0].bindings)
    assert covered == {("util.py", "util.py::a"), ("util.py", "util.py::b")}


# ---------------------------------------------------------------------------
# Two-phase orchestration
# ---------------------------------------------------------------------------

def test_one_file_call_per_file_with_scoped_chunks(store):
    """propose_file is called once per file, seeing only that file's chunks."""
    rows = [
        FakeRow("a.py", "a.py::af"),
        FakeRow("b.py", "b.py::bf"),
        FakeRow("b.py", "b.py::bg"),
    ]
    build_graph(store, rows)

    seen: list[tuple[str, set]] = []

    def propose_file(file, chunks, edges, existing_titles, *, repo_name, config, why=None):
        seen.append((file, {c["symbol_path"] for c in chunks}))
        return [_add("n1", f"Feature {file}", [(file, c["symbol_path"]) for c in chunks])]

    bootstrap_hier_from_chunks(
        rows, store, propose_file=propose_file, propose_org=lambda *a, **k: [], organize=True
    )

    assert {f for f, _ in seen} == {"a.py", "b.py"}
    by_file = dict(seen)
    assert by_file["a.py"] == {"a.py::af"}              # no cross-file leakage
    assert by_file["b.py"] == {"b.py::bf", "b.py::bg"}


def test_org_pass_groups_top_level_features(store):
    rows = [FakeRow("a.py", "a.py::af"), FakeRow("b.py", "b.py::bf")]
    build_graph(store, rows)

    def propose_file(file, chunks, edges, existing_titles, *, repo_name, config, why=None):
        return [_add("n1", f"Feat {file}", [(file, c["symbol_path"]) for c in chunks])]

    def propose_org(features, edges, *, repo_name, config):
        ops = [_add("t1", "Theme", [])]
        for f in features:
            ops.append(NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id=f["id"], parent_id="t1"))
        return ops

    bootstrap_hier_from_chunks(rows, store, propose_file=propose_file, propose_org=propose_org)

    top = store.children(None)
    assert len(top) == 1 and top[0].title == "Theme"
    assert {c.title for c in store.children(top[0].id)} == {"Feat a.py", "Feat b.py"}


def test_org_skipped_for_single_top_level_feature(store):
    rows = [FakeRow("a.py", "a.py::af")]
    build_graph(store, rows)
    called = []

    def propose_file(file, chunks, edges, existing_titles, *, repo_name, config, why=None):
        return [_add("n1", "Only", [(file, c["symbol_path"]) for c in chunks])]

    def propose_org(features, edges, *, repo_name, config):
        called.append(True)
        return []

    res = bootstrap_hier_from_chunks(rows, store, propose_file=propose_file, propose_org=propose_org)
    assert not called, "org pass should not run with a single top-level feature"
    assert res.batches == 1


def test_feature_coupling_lines(store):
    """Cross-feature call edges aggregate into coupling lines for the org pass."""
    rows = [
        FakeRow("a.py", "a.py::caller", "def caller():\n    callee()\n"),
        FakeRow("b.py", "b.py::callee", "def callee():\n    return 1\n"),
    ]
    build_graph(store, rows)
    _apply_ops_with_local_ids(
        [_add("n1", "A", [("a.py", "a.py::caller")])], store, {}, source="bootstrap"
    )
    _apply_ops_with_local_ids(
        [_add("n2", "B", [("b.py", "b.py::callee")])], store, {}, source="bootstrap"
    )

    lines = _feature_coupling(store)
    assert any("(A)" in ln and "(B)" in ln for ln in lines), lines


def test_empty_repo(store):
    res = bootstrap_hier_from_chunks(
        [], store, propose_file=lambda *a, **k: [], propose_org=lambda *a, **k: []
    )
    assert res.chunks == 0 and res.features == 0 and res.batches == 0


def test_every_chunk_bound_after_bootstrap(store):
    """No chunk is left unattributed across the whole two-phase run."""
    rows = [
        FakeRow("a.py", "a.py::Big"),
        FakeRow("a.py", "a.py::Big.m"),
        FakeRow("b.py", "b.py::fn"),
    ]
    build_graph(store, rows)

    def propose_file(file, chunks, edges, existing_titles, *, repo_name, config, why=None):
        # Cover only the first chunk; coverage net must catch the rest.
        first = chunks[0]
        return [_add("n1", f"F {file}", [(file, first["symbol_path"])])]

    bootstrap_hier_from_chunks(
        rows, store, propose_file=propose_file, propose_org=lambda *a, **k: []
    )

    bound = {(b.file, b.symbol_path) for b in store.all_bindings()}
    assert bound == {(r.file, r.symbol_path) for r in rows}
