"""Phase 1 — data model + 3-table store."""
from __future__ import annotations

import pytest

from codoc.model.binding import Binding
from codoc.model.event import Event, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import Store, open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


# -- features ------------------------------------------------------------
def test_feature_roundtrip(store):
    f = Feature(title="Index snapshot diff", description="Diffs the index.")
    store.upsert_feature(f)
    got = store.get_feature(f.id)
    assert got is not None
    assert got.title == "Index snapshot diff"
    assert got.description == "Diffs the index."
    assert got.parent_id is None
    assert got.retired is False


def test_feature_ids_are_prefixed_and_unique():
    a = Feature(title="A")
    b = Feature(title="B")
    assert a.id.startswith("f-")
    assert a.id != b.id


def test_upsert_feature_updates(store):
    f = Feature(title="Old title")
    store.upsert_feature(f)
    f.title = "New title"
    f.description = "now described"
    store.upsert_feature(f)
    got = store.get_feature(f.id)
    assert got.title == "New title"
    assert got.description == "now described"
    # still a single row
    assert len(store.list_features()) == 1


def test_children_and_retire(store):
    root = Feature(title="Root")
    child = Feature(title="Child", parent_id=root.id)
    store.upsert_feature(root)
    store.upsert_feature(child)

    assert [c.id for c in store.children(None)] == [root.id]
    assert [c.id for c in store.children(root.id)] == [child.id]

    store.retire_feature(child.id)
    assert store.children(root.id) == []
    assert store.get_feature(child.id).retired is True
    # retired excluded by default, included on request
    assert [f.id for f in store.list_features()] == [root.id]
    assert {f.id for f in store.list_features(include_retired=True)} == {root.id, child.id}


# -- bindings ------------------------------------------------------------
def test_binding_roundtrip_and_anchor_lookup(store):
    f = Feature(title="F")
    store.upsert_feature(f)
    b = Binding(feature_id=f.id, file="a.py", symbol_path="a.py::foo", fingerprint="h1")
    store.upsert_binding(b)

    got = store.binding_at("a.py", "a.py::foo")
    assert got is not None and got.feature_id == f.id and got.fingerprint == "h1"
    assert [x.id for x in store.bindings_for_feature(f.id)] == [got.id]


def test_binding_unique_anchor_rebinds(store):
    f1 = Feature(title="F1")
    f2 = Feature(title="F2")
    store.upsert_feature(f1)
    store.upsert_feature(f2)

    store.upsert_binding(Binding(feature_id=f1.id, file="a.py", symbol_path="a.py::foo", fingerprint="h1"))
    # same anchor, new owner + fingerprint → updates in place, no duplicate row
    store.upsert_binding(Binding(feature_id=f2.id, file="a.py", symbol_path="a.py::foo", fingerprint="h2"))

    assert len(store.all_bindings()) == 1
    got = store.binding_at("a.py", "a.py::foo")
    assert got.feature_id == f2.id
    assert got.fingerprint == "h2"
    assert store.bindings_for_feature(f1.id) == []


def test_delete_binding_and_bindings_in_files(store):
    f = Feature(title="F")
    store.upsert_feature(f)
    store.upsert_binding(Binding(feature_id=f.id, file="a.py", symbol_path="a.py::foo", fingerprint="h"))
    store.upsert_binding(Binding(feature_id=f.id, file="b.py", symbol_path="b.py::bar", fingerprint="h"))

    assert {b.file for b in store.bindings_in_files({"a.py"})} == {"a.py"}
    store.delete_binding("a.py", "a.py::foo")
    assert store.binding_at("a.py", "a.py::foo") is None
    assert len(store.all_bindings()) == 1


# -- events --------------------------------------------------------------
def test_event_proposal_lifecycle(store):
    op = NodeOp(kind=NodeOpKind.ADD_NODE, title="New thing", description="desc", rationale="no node fits")
    e = Event(source="loop_a", op=op, applied=False)
    store.append_event(e)

    pending = store.pending_events()
    assert [p.id for p in pending] == [e.id]
    got = pending[0]
    assert got.op.kind is NodeOpKind.ADD_NODE
    assert got.op.title == "New thing"
    assert got.is_proposal is True

    store.mark_applied(e.id)
    assert store.pending_events() == []
    reloaded = store.get_event(e.id)
    assert reloaded.applied is True
    assert reloaded.accepted_at is not None


def test_event_op_bindings_roundtrip(store):
    op = NodeOp(
        kind=NodeOpKind.ATTACH,
        feature_id="f-123",
        bindings=[("a.py", "a.py::foo"), ("a.py", "a.py::bar")],
    )
    e = Event(source="loop_a", op=op, applied=True)
    store.append_event(e)
    got = store.get_event(e.id).op
    assert got.bindings == [("a.py", "a.py::foo"), ("a.py", "a.py::bar")]


def test_delete_event(store):
    e = Event(source="user", op=NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id="f-1"), applied=False)
    store.append_event(e)
    store.delete_event(e.id)
    assert store.get_event(e.id) is None
    assert store.pending_events() == []


def test_store_reopen_persists(tmp_path):
    s = open_store(tmp_path)
    f = Feature(title="Persisted")
    s.upsert_feature(f)
    s.close()

    s2 = Store(tmp_path / "codoc.db").open()
    assert s2.get_feature(f.id).title == "Persisted"
    s2.close()
