"""Phase 3 — ego-graph subtree selection + Phase 4 may-impact (no LLM, no index)."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from codoc.graph.query import build_graph
from codoc.loop.diff import ChangeSet, ChunkRef
from codoc.loop.subtree import select_relevant_subtree
from codoc.model.binding import Binding
from codoc.model.feature import Feature
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
    minhash: bytes = b""
    start_byte: int = 0
    end_byte: int = 0
    embedding: object = None


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_changeset(added=None, modified=None, removed=None, rows=None):
    return ChangeSet(
        added=added or [],
        modified=modified or [],
        removed=removed or [],
        rows=rows or [],
    )


# ---------------------------------------------------------------------------
# File-locality (baseline — still works after ego upgrade)
# ---------------------------------------------------------------------------

def test_file_locality_seeds_included(store):
    feat = Feature(title="Auth", description="handles auth")
    store.upsert_feature(feat)
    store.upsert_binding(
        Binding(feature_id=feat.id, file="auth.py", symbol_path="auth.py::login", fingerprint="h1")
    )

    cs = _make_changeset(modified=[ChunkRef("auth.py", "auth.py::login", "h2", "")])
    subtree, all_titles, ctx = select_relevant_subtree(cs, store)
    ids = {s["id"] for s in subtree}
    assert feat.id in ids


# ---------------------------------------------------------------------------
# Phase 3: ego-graph cross-file expansion
# ---------------------------------------------------------------------------

def test_ego_expansion_pulls_callee_feature(store):
    """Changed a.py::foo calls b.py::bar (untouched file) → bar's feature in subtree."""
    bar_feat = Feature(title="Bar feature", description="bar impl")
    store.upsert_feature(bar_feat)
    store.upsert_binding(
        Binding(feature_id=bar_feat.id, file="b.py", symbol_path="b.py::bar", fingerprint="h1")
    )

    rows = [
        FakeRow("a.py", "a.py::foo", "def foo():\n    bar()\n"),
        FakeRow("b.py", "b.py::bar", "def bar():\n    return 1\n"),
    ]
    build_graph(store, rows)

    # Only a.py is in the changeset — b.py was not touched
    cs = _make_changeset(
        added=[ChunkRef("a.py", "a.py::foo", "h2", "def foo():\n    bar()\n")],
        rows=rows,
    )
    subtree, all_titles, ctx = select_relevant_subtree(cs, store)

    subtree_ids = {s["id"] for s in subtree}
    assert bar_feat.id in subtree_ids, (
        "Ego expansion should pull bar's feature via the call edge, "
        f"even though b.py wasn't touched. Got subtree: {subtree}"
    )


def test_ego_expansion_pulls_caller_feature(store):
    """Changed b.py::bar (callee); caller a.py::foo (untouched) → foo's feature in subtree."""
    foo_feat = Feature(title="Foo feature", description="foo impl")
    store.upsert_feature(foo_feat)
    store.upsert_binding(
        Binding(feature_id=foo_feat.id, file="a.py", symbol_path="a.py::foo", fingerprint="h1")
    )

    rows = [
        FakeRow("a.py", "a.py::foo", "def foo():\n    bar()\n"),
        FakeRow("b.py", "b.py::bar", "def bar():\n    return 1\n"),
    ]
    build_graph(store, rows)

    cs = _make_changeset(
        modified=[ChunkRef("b.py", "b.py::bar", "h2", "def bar():\n    return 2\n")],
        rows=rows,
    )
    subtree, all_titles, ctx = select_relevant_subtree(cs, store)

    subtree_ids = {s["id"] for s in subtree}
    assert foo_feat.id in subtree_ids, (
        "Ego expansion should pull foo's feature (caller of changed bar). "
        f"Got subtree: {subtree}"
    )


def test_ego_no_expansion_without_graph(store):
    """No graph edges → falls back to file-locality only (no crash)."""
    feat = Feature(title="A feature", description="some feature")
    store.upsert_feature(feat)
    store.upsert_binding(
        Binding(feature_id=feat.id, file="a.py", symbol_path="a.py::fn", fingerprint="h1")
    )

    cs = _make_changeset(modified=[ChunkRef("a.py", "a.py::fn", "h2", "")])
    # No build_graph call → empty code_edges table
    subtree, all_titles, ctx = select_relevant_subtree(cs, store)
    ids = {s["id"] for s in subtree}
    assert feat.id in ids


# ---------------------------------------------------------------------------
# Phase 3: context block (edges + recent)
# ---------------------------------------------------------------------------

def test_context_edges_populated(store):
    """When a call edge exists, it appears in context['edges']."""
    rows = [
        FakeRow("a.py", "a.py::foo", "def foo():\n    bar()\n"),
        FakeRow("b.py", "b.py::bar", "def bar():\n    return 1\n"),
    ]
    build_graph(store, rows)

    cs = _make_changeset(
        added=[ChunkRef("a.py", "a.py::foo", "h1", "def foo():\n    bar()\n")],
        rows=rows,
    )
    _, _, ctx = select_relevant_subtree(cs, store)

    assert "edges" in ctx
    # The call edge foo→bar should appear
    call_edges = [e for e in ctx["edges"] if e["kind"] == "call"]
    assert any(
        e["from"] == "a.py::foo" and e["to"] == "b.py::bar" for e in call_edges
    ), f"Expected call edge foo→bar; got {call_edges}"


def test_context_edges_empty_without_graph(store):
    cs = _make_changeset(added=[ChunkRef("a.py", "a.py::fn", "h1", "")])
    _, _, ctx = select_relevant_subtree(cs, store)
    assert ctx["edges"] == []


def test_context_recent_filtered_to_seeds(store):
    """recent events are filtered to seed features only."""
    from codoc.loop.apply import apply_op
    from codoc.model.event import NodeOp, NodeOpKind

    feat = Feature(title="Auth", description="")
    other = Feature(title="Other", description="")
    store.upsert_feature(feat)
    store.upsert_feature(other)
    store.upsert_binding(
        Binding(feature_id=feat.id, file="auth.py", symbol_path="auth.py::login", fingerprint="h1")
    )

    # Record an event for `feat`
    op = NodeOp(kind=NodeOpKind.AMEND, feature_id=feat.id, description="updated")
    apply_op(op, store, source="test", applied=True, fp_lookup={})

    cs = _make_changeset(modified=[ChunkRef("auth.py", "auth.py::login", "h2", "")])
    _, _, ctx = select_relevant_subtree(cs, store)

    # recent should only contain events for feat (a seed), not other
    fids = {r["feature_id"] for r in ctx["recent"]}
    assert feat.id in fids
    assert other.id not in fids


# ---------------------------------------------------------------------------
# Phase 4 via loop_a: may-impact in changes dict
# ---------------------------------------------------------------------------

def test_may_impact_surfaces_caller_feature(store):
    """Modifying b.py::bar (callee) surfaces a.py::foo's feature as impacted."""
    from codoc.loop.loop_a import apply_changeset

    foo_feat = Feature(title="Foo feature", description="calls bar")
    bar_feat = Feature(title="Bar feature", description="bar impl")
    store.upsert_feature(foo_feat)
    store.upsert_feature(bar_feat)
    store.upsert_binding(
        Binding(feature_id=foo_feat.id, file="a.py", symbol_path="a.py::foo", fingerprint="h1")
    )
    store.upsert_binding(
        Binding(feature_id=bar_feat.id, file="b.py", symbol_path="b.py::bar", fingerprint="h2")
    )

    rows = [
        FakeRow("a.py", "a.py::foo", "def foo():\n    bar()\n"),
        FakeRow("b.py", "b.py::bar", "def bar():\n    return 1\n"),
    ]
    build_graph(store, rows)

    received_changes: list[dict] = []

    def propose(changes, subtree, all_titles, *, repo_name="x", config=None):
        received_changes.append(changes)
        return []

    cs = _make_changeset(
        modified=[ChunkRef("b.py", "b.py::bar", "h3", "def bar():\n    return 99\n")],
        rows=rows,
    )
    result = apply_changeset(cs, store, source="test", propose=propose)

    assert result.impacted, "Expected impacted list to be non-empty"
    assert foo_feat.id in result.impacted, (
        f"foo_feat ({foo_feat.id}) should be in impacted list: {result.impacted}"
    )

    if received_changes:
        impacted_block = received_changes[0].get("impacted", [])
        fids = {e["feature_id"] for e in impacted_block}
        assert foo_feat.id in fids, f"impacted block should include foo_feat; got {impacted_block}"


def test_may_impact_empty_when_no_dependents(store):
    """No callers → impacted list is empty, no crash."""
    from codoc.loop.loop_a import apply_changeset

    feat = Feature(title="Leaf", description="no callers")
    store.upsert_feature(feat)
    store.upsert_binding(
        Binding(feature_id=feat.id, file="a.py", symbol_path="a.py::fn", fingerprint="h1")
    )
    build_graph(store, [FakeRow("a.py", "a.py::fn", "def fn(): pass")])

    cs = _make_changeset(modified=[ChunkRef("a.py", "a.py::fn", "h2", "def fn(): return 1")])
    result = apply_changeset(cs, store, source="test", propose=lambda *a, **k: [])
    assert result.impacted == []
