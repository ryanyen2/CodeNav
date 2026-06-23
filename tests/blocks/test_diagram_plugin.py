"""U5 — the diagram codec: deterministic lift from the graph, deterministic
edge-delta lower, and the draft fallback for unmappable edits."""
from __future__ import annotations

import pytest

from codoc.blocks.base import LiftContext, LowerContext
from codoc.blocks.diagram import DiagramPlugin
from codoc.model.binding import Binding
from codoc.model.block import Block
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _edge(store, src, dst):
    store.insert_edges([{
        "src_file": "a.py", "src_symbol": src, "dst_name": dst.split("::")[-1],
        "dst_symbol": dst, "dst_file": "a.py", "kind": "call", "internal": 1,
    }])


def test_lift_renders_bound_neighborhood(store):
    f = Feature(title="Auth")
    store.upsert_feature(f)
    b = Binding(feature_id=f.id, file="a.py", symbol_path="a.py::login", fingerprint="h")
    store.upsert_binding(b)
    _edge(store, "a.py::login", "a.py::make_token")
    out = DiagramPlugin().lift(LiftContext(feature=f, bindings=[b], store=store))
    assert out.changed
    assert "flowchart TB" in out.content
    assert "login --> make_token" in out.content


def test_lift_no_change_when_identical(store):
    f = Feature(title="Auth")
    store.upsert_feature(f)
    b = Binding(feature_id=f.id, file="a.py", symbol_path="a.py::login", fingerprint="h")
    store.upsert_binding(b)
    _edge(store, "a.py::login", "a.py::make_token")
    p = DiagramPlugin()
    first = p.lift(LiftContext(feature=f, bindings=[b], store=store))
    blk = Block(feature_id=f.id, kind="diagram", content=first.content)
    again = p.lift(LiftContext(feature=f, bindings=[b], store=store, block=blk))
    assert not again.changed


def test_lower_removed_edge_becomes_directive():
    f = Feature(title="Auth")
    b = Binding(feature_id=f.id, file="a.py", symbol_path="a.py::login", fingerprint="h")
    old = Block(feature_id=f.id, kind="diagram", content="flowchart TB\n  login --> make_token")
    new = Block(id=old.id, feature_id=f.id, kind="diagram", content="flowchart TB\n  login")
    res = DiagramPlugin().lower(LowerContext(feature=f, old_block=old, new_block=new, bindings=[b]))
    assert res.kind == "directive"
    assert "Remove the dependency" in res.text
    assert "login" in res.text and "make_token" in res.text


def test_lower_added_edge_becomes_directive():
    f = Feature(title="Auth")
    b = Binding(feature_id=f.id, file="a.py", symbol_path="a.py::login", fingerprint="h")
    old = Block(feature_id=f.id, kind="diagram", content="flowchart TB\n  login")
    new = Block(id=old.id, feature_id=f.id, kind="diagram", content="flowchart TB\n  login --> audit")
    res = DiagramPlugin().lower(LowerContext(feature=f, old_block=old, new_block=new, bindings=[b]))
    assert res.kind == "directive"
    assert "Add a dependency" in res.text


def test_lower_unmappable_edit_is_draft():
    f = Feature(title="Auth")
    b = Binding(feature_id=f.id, file="a.py", symbol_path="a.py::login", fingerprint="h")
    old = Block(feature_id=f.id, kind="diagram", content="flowchart TB\n  login --> audit")
    new = Block(id=old.id, feature_id=f.id, kind="diagram",
                content="flowchart TB\n  login --> audit\n  X[some freeform node]")
    res = DiagramPlugin().lower(LowerContext(feature=f, old_block=old, new_block=new, bindings=[b]))
    assert res.kind == "draft"


def test_lower_unbound_is_noop():
    f = Feature(title="Ambient")
    old = Block(feature_id=f.id, kind="diagram", content="flowchart TB\n  a --> b")
    new = Block(id=old.id, feature_id=f.id, kind="diagram", content="flowchart TB\n  a")
    res = DiagramPlugin().lower(LowerContext(feature=f, old_block=old, new_block=new, bindings=[]))
    assert res.kind == "noop"
