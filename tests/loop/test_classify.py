"""The classification decision table (loop/classify.py) — row-by-row cases."""
from __future__ import annotations

import pytest

from codoc.loop.apply import apply_op
from codoc.loop.classify import implies_code, is_imperative, suppressed_by_hold
from codoc.model.binding import Binding
from codoc.model.event import (
    ACTOR_HUMAN,
    ACTOR_LOOP,
    DEFAULT_AGENT_ACTOR,
    MODE_AUTO,
    MODE_PEN,
    MODE_SUGGEST,
    NodeOp,
    NodeOpKind,
    default_provenance,
)
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


# -- rows 7/8: the imperative gate ----------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("Validates request headers before dispatch.", False),       # descriptive 3rd person
    ("Add retry logic with exponential backoff.", True),         # sentence-initial bare verb
    ("The parser should reject unterminated strings.", True),    # obligation cue
    ("TODO: cover the unicode path.", True),                     # TODO cue
    ("Handles retries; adds backoff.", False),                   # 3rd person, no cue
    ("", False),
    (None, False),
])
def test_is_imperative(text, expected):
    assert is_imperative(text) is expected


def test_descriptive_amend_is_row_7_no_directive(store):
    op = NodeOp(kind=NodeOpKind.AMEND, feature_id="f-1",
                description="Parses the tree file into nodes.")
    assert implies_code(op, store) is False


def test_imperative_amend_is_row_8_directive(store):
    op = NodeOp(kind=NodeOpKind.AMEND, feature_id="f-1",
                description="Rewrite the parser; it should reject tabs.")
    assert implies_code(op, store) is True


def test_plan_placeholder_add_is_row_8_directive(store):
    op = NodeOp(kind=NodeOpKind.ADD_NODE, title="Rate limiting",
                description="Caps request rates per client.", realized=False)
    assert implies_code(op, store) is True


def test_descriptive_add_is_row_7_node_only(store):
    op = NodeOp(kind=NodeOpKind.ADD_NODE, title="Rate limiting",
                description="Caps request rates per client.")
    assert implies_code(op, store) is False


def test_retire_with_bound_code_is_row_8(store):
    f = Feature(title="Old path")
    store.upsert_feature(f)
    store.upsert_binding(Binding(feature_id=f.id, file="a.py",
                                 symbol_path="a.py::old", fingerprint="x"))
    op = NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=f.id)
    assert implies_code(op, store) is True
    # unbound feature: nothing to remove → no directive
    g = Feature(title="Empty")
    store.upsert_feature(g)
    assert implies_code(NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=g.id), store) is False


# -- row 13: doc-wins holds ------------------------------------------------
def test_hold_suppresses_intent_ops_only():
    held = {"f-held"}
    amend = NodeOp(kind=NodeOpKind.AMEND, feature_id="f-held", description="x")
    retire = NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id="f-held")
    move = NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id="f-held", parent_id="f-p")
    assert suppressed_by_hold(amend, held) is True
    assert suppressed_by_hold(retire, held) is True
    assert suppressed_by_hold(move, held) is True
    # binding maintenance is never suppressed (rows 1/3)
    attach = NodeOp(kind=NodeOpKind.ATTACH, feature_id="f-held", bindings=[("a.py", "a.py::f")])
    refresh = NodeOp(kind=NodeOpKind.REFRESH, feature_id="f-held", bindings=[("a.py", "a.py::f")])
    detach = NodeOp(kind=NodeOpKind.DETACH, feature_id="f-held", bindings=[("a.py", "a.py::f")])
    assert suppressed_by_hold(attach, held) is False
    assert suppressed_by_hold(refresh, held) is False
    assert suppressed_by_hold(detach, held) is False
    # other features unaffected
    other = NodeOp(kind=NodeOpKind.AMEND, feature_id="f-free", description="x")
    assert suppressed_by_hold(other, held) is False
    assert suppressed_by_hold(amend, set()) is False


# -- provenance stamping -----------------------------------------------------
@pytest.mark.parametrize("source,applied,expected", [
    ("user", True, (ACTOR_HUMAN, MODE_PEN)),
    ("loop_a", True, (ACTOR_LOOP, MODE_AUTO)),
    ("loop_a", False, (ACTOR_LOOP, MODE_SUGGEST)),
    ("bootstrap", True, (ACTOR_LOOP, MODE_AUTO)),
    ("loop_a_agent", True, (DEFAULT_AGENT_ACTOR, MODE_AUTO)),
    ("loop_a_agent", False, (DEFAULT_AGENT_ACTOR, MODE_SUGGEST)),
    ("plan", False, (DEFAULT_AGENT_ACTOR, MODE_SUGGEST)),
])
def test_default_provenance(source, applied, expected):
    assert default_provenance(source, applied) == expected


def test_apply_op_stamps_default_provenance(store):
    f = Feature(title="Thing")
    store.upsert_feature(f)
    e = apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id, description="d"),
                 store, source="user", applied=True)
    got = store.get_event(e.id)
    assert (got.actor, got.mode) == (ACTOR_HUMAN, MODE_PEN)


def test_apply_op_explicit_provenance_wins(store):
    f = Feature(title="Thing")
    store.upsert_feature(f)
    e = apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id, description="d"),
                 store, source="user", applied=True,
                 actor="codex", mode=MODE_SUGGEST, caused_by="d-1234abcd")
    got = store.get_event(e.id)
    assert (got.actor, got.mode, got.caused_by) == ("codex", MODE_SUGGEST, "d-1234abcd")
