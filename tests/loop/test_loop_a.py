"""Phase 2 — Loop A routing + apply logic (real store, mocked LLM)."""
from __future__ import annotations

import pytest

from codoc.loop.diff import ChangeSet, ChunkRef
from codoc.loop.loop_a import apply_changeset
from codoc.model.binding import Binding
from codoc.model.event import NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _propose(ops):
    def p(changes, subtree, all_titles, *, repo_name="codebase", config=None):
        return list(ops)
    return p


def _raising(*a, **k):
    raise AssertionError("LLM should not be called")


def _feature(store, **kw) -> Feature:
    f = Feature(**kw)
    store.upsert_feature(f)
    return f


def _bind(store, fid, file, symbol, fp="old"):
    store.upsert_binding(Binding(feature_id=fid, file=file, symbol_path=symbol, fingerprint=fp))


# -----------------------------------------------------------------------
def test_empty_changeset_noop(store):
    res = apply_changeset(ChangeSet(), store, propose=_raising)
    assert res.auto == {} and not res.llm_called and not res.proposed


def test_modified_bound_refreshes_without_llm(store):
    f = _feature(store, title="Foo")
    _bind(store, f.id, "a.py", "a.py::foo", fp="old")
    cs = ChangeSet(modified=[ChunkRef("a.py", "a.py::foo", "new", "def foo(): ...")])

    res = apply_changeset(cs, store, propose=_raising)

    assert not res.llm_called
    assert res.auto == {"refresh": 1}
    assert store.binding_at("a.py", "a.py::foo").fingerprint == "new"


def test_removed_binding_not_emptying_skips_llm(store):
    f = _feature(store, title="Foo")
    _bind(store, f.id, "a.py", "a.py::foo")
    _bind(store, f.id, "a.py", "a.py::bar")
    cs = ChangeSet(removed=[ChunkRef("a.py", "a.py::foo")])

    res = apply_changeset(cs, store, propose=_raising)

    assert not res.llm_called
    assert res.auto == {"detach": 1}
    assert store.binding_at("a.py", "a.py::foo") is None
    assert len(store.bindings_for_feature(f.id)) == 1


def test_removed_binding_empties_feature_triggers_llm_retire(store):
    f = _feature(store, title="Lonely")
    _bind(store, f.id, "a.py", "a.py::foo")
    cs = ChangeSet(removed=[ChunkRef("a.py", "a.py::foo")])

    ops = [NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=f.id, rationale="all code gone")]
    res = apply_changeset(cs, store, propose=_propose(ops))

    assert res.llm_called
    assert res.auto == {"detach": 1}
    # structural retire is a pending proposal, NOT yet applied
    assert store.get_feature(f.id).retired is False
    assert len(store.pending_events()) == 1
    assert res.proposed[0].kind is NodeOpKind.RETIRE_NODE


def test_added_unbound_attach_is_safe(store):
    f = _feature(store, title="Foo")
    _bind(store, f.id, "a.py", "a.py::foo")
    cs = ChangeSet(added=[ChunkRef("a.py", "a.py::bar", "fpbar", "def bar(): ...")])

    ops = [NodeOp(kind=NodeOpKind.ATTACH, feature_id=f.id, bindings=[("a.py", "a.py::bar")])]
    res = apply_changeset(cs, store, propose=_propose(ops))

    assert res.llm_called and not res.proposed
    got = store.binding_at("a.py", "a.py::bar")
    assert got is not None and got.feature_id == f.id and got.fingerprint == "fpbar"


def test_added_unbound_add_node_is_structural(store):
    cs = ChangeSet(added=[ChunkRef("new.py", "new.py::thing", "fp", "class Thing: ...")])
    ops = [NodeOp(kind=NodeOpKind.ADD_NODE, title="New thing", description="does a thing",
                  bindings=[("new.py", "new.py::thing")], rationale="no node fits")]

    res = apply_changeset(cs, store, propose=_propose(ops))

    assert res.proposed and res.proposed[0].kind is NodeOpKind.ADD_NODE
    assert store.list_features() == []                 # not created until accepted
    assert store.binding_at("new.py", "new.py::thing") is None
    assert len(store.pending_events()) == 1


def test_small_amend_autoapplies_large_amend_proposed(store):
    f = _feature(store, title="Foo", description="the quick brown fox jumps over")
    _bind(store, f.id, "a.py", "a.py::foo")
    # an unbound add forces the LLM pass; the amend targets the existing feature
    cs = ChangeSet(
        added=[ChunkRef("a.py", "a.py::new", "fp", "src")],
        modified=[ChunkRef("a.py", "a.py::foo", "new", "src")],
    )

    small = NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id,
                   description="the quick brown fox jumped over")  # ~1 char change
    res = apply_changeset(cs, store, propose=_propose([small]))
    assert not res.proposed
    assert store.get_feature(f.id).description == "the quick brown fox jumped over"

    large = NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id,
                   description="completely different prose describing an entirely new responsibility set")
    res2 = apply_changeset(cs, store, propose=_propose([large]))
    assert res2.proposed and res2.proposed[0].kind is NodeOpKind.AMEND
    # unchanged because the large amend is only a proposal
    assert store.get_feature(f.id).description == "the quick brown fox jumped over"
