"""Tests for the "agent plan" / "code drift" source annotation in render.py."""
from __future__ import annotations

import pytest

from codoc.codoc_file.diff import diff_codoc
from codoc.codoc_file.parse import parse_text
from codoc.codoc_file.render import _proposals_map, render_tree
from codoc.model.event import PLAN_SOURCE, Event, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _add_proposal(store, source: str, kind=NodeOpKind.ADD_NODE, **op_kw):
    op = NodeOp(kind=kind, title="Widget", description="A UI widget.", **op_kw)
    e = Event(source=source, applied=False, op=op)
    store.append_event(e)
    return e


# ── Source tag annotation ──────────────────────────────────────────────────────

def test_plan_proposal_renders_agent_plan_tag(store):
    _add_proposal(store, source=PLAN_SOURCE)
    text = render_tree(store)
    assert "agent plan" in text
    assert "code drift" not in text


def test_code_drift_proposal_renders_code_drift_tag(store):
    _add_proposal(store, source="loop_a")
    text = render_tree(store)
    assert "code drift" in text
    assert "agent plan" not in text


def test_loop_b_proposal_renders_code_drift_tag(store):
    _add_proposal(store, source="loop_b")
    text = render_tree(store)
    assert "code drift" in text


def test_retire_node_plan_tag(store):
    f = Feature(title="Old feature")
    store.upsert_feature(f)
    op = NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=f.id)
    e = Event(source=PLAN_SOURCE, applied=False, op=op)
    store.append_event(e)
    # Retire decorates the live node in place → sidecar, not text.
    entry = _proposals_map(store)["by_feature"][f.id]
    assert entry["op"] == "retire"
    assert entry["tag"] == "agent plan"
    # The live node renders exactly once (no duplicate "ghost" retire line).
    assert render_tree(store).count("Old feature") == 1


def test_move_node_plan_tag(store):
    parent = Feature(title="Parent")
    child = Feature(title="Child")
    store.upsert_feature(parent)
    store.upsert_feature(child)
    op = NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id=child.id, parent_id=parent.id)
    e = Event(source=PLAN_SOURCE, applied=False, op=op)
    store.append_event(e)
    text = render_tree(store)
    assert "agent plan" in text


def test_amend_plan_tag(store):
    f = Feature(title="Feature")
    store.upsert_feature(f)
    op = NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id, description="New description.")
    e = Event(source=PLAN_SOURCE, applied=False, op=op)
    store.append_event(e)
    # Amend decorates the live node in place → sidecar, with the proposed prose.
    entry = _proposals_map(store)["by_feature"][f.id]
    assert entry["op"] == "amend"
    assert entry["tag"] == "agent plan"
    assert entry["description"] == "New description."


# ── Round-trip invariant still holds with plan proposals ──────────────────────

def test_plan_proposal_roundtrip_noop(store):
    """render → parse_text → diff_codoc yields no user_ops (round-trip invariant)."""
    f = Feature(title="Root")
    store.upsert_feature(f)
    _add_proposal(store, source=PLAN_SOURCE)

    text = render_tree(store)
    diff = diff_codoc(parse_text(text), store)
    assert diff.is_empty(), f"plan proposal leaked into user ops: {diff}"


def test_mixed_proposals_roundtrip_noop(store):
    """Both plan and loop_a proposals together must still round-trip cleanly."""
    f = Feature(title="Root")
    store.upsert_feature(f)
    _add_proposal(store, source=PLAN_SOURCE)
    _add_proposal(store, source="loop_a")

    text = render_tree(store)
    diff = diff_codoc(parse_text(text), store)
    assert diff.is_empty(), f"mixed proposals leaked: {diff}"
