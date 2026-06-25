"""The classification decision table (loop/classify.py) — row-by-row cases."""
from __future__ import annotations

import pytest

from codoc.loop.apply import apply_op
from codoc.loop.classify import edit_mints_directive, suppressed_by_hold
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


# -- rows 7/8: the STRUCTURAL directive gate (no prose heuristic) -----------
# is_imperative is DELETED. edit_mints_directive decides STRUCTURALLY whether a tree
# edit mints a directive at all — never by inspecting English mood. Whether the minted
# directive realizes now or is held as a draft is the finalize hand-off decision
# (tested in test_loop_b.py / test_u2b_single_writer.py).

@pytest.mark.parametrize("description", [
    "Parses the tree file into nodes.",          # descriptive 3rd person
    "Rewrite the parser; it should reject tabs.",  # was "imperative" — now no special case
    "Add retry logic.",                          # sentence-initial verb — no special case
    "",
    None,
])
def test_amend_always_mints_a_directive(store, description):
    """Every AMEND mints a directive (held as a draft by default) — the SYSTEM never
    guesses from prose whether the edit 'requests code'. No more false positives on
    descriptive prose that opens with a verb, no more typo-fix re-fires."""
    op = NodeOp(kind=NodeOpKind.AMEND, feature_id="f-1", description=description)
    assert edit_mints_directive(op, store) is True


def test_plan_placeholder_add_mints_directive(store):
    op = NodeOp(kind=NodeOpKind.ADD_NODE, title="Rate limiting",
                description="Caps request rates per client.", realized=False)
    assert edit_mints_directive(op, store) is True


def test_descriptive_add_is_node_only(store):
    """A hand-added node that is NOT an explicit plan (realized defaults True) is a
    node, not a build request — no directive. The 'plan' authoring gesture sets
    realized=False to request a build."""
    op = NodeOp(kind=NodeOpKind.ADD_NODE, title="Rate limiting",
                description="Caps request rates per client.")
    assert edit_mints_directive(op, store) is False


def test_retire_with_bound_code_mints_directive(store):
    f = Feature(title="Old path")
    store.upsert_feature(f)
    store.upsert_binding(Binding(feature_id=f.id, file="a.py",
                                 symbol_path="a.py::old", fingerprint="x"))
    op = NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=f.id)
    assert edit_mints_directive(op, store) is True
    # unbound feature: nothing to remove → no directive
    g = Feature(title="Empty")
    store.upsert_feature(g)
    assert edit_mints_directive(NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=g.id), store) is False


def test_move_never_mints_directive(store):
    op = NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id="f-1", parent_id="f-p")
    assert edit_mints_directive(op, store) is False


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
