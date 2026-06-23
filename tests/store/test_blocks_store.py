"""U1 — the blocks table: CRUD, ordering, identity round-trip, and proof that the
UNIQUE(file, symbol_path) binding invariant is untouched (KTD1)."""
from __future__ import annotations

import pytest

from codoc.model.binding import Binding
from codoc.model.block import Block, BlockLifecycle, Provenance
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def test_block_crud_roundtrip(store):
    f = Feature(title="Auth")
    store.upsert_feature(f)
    b = Block(feature_id=f.id, kind="diagram", content="flowchart TB\nA-->B",
              provenance=Provenance.DERIVED)
    store.upsert_block(b)
    got = store.get_block(b.id)
    assert got is not None
    assert got.kind == "diagram"
    assert got.content == "flowchart TB\nA-->B"
    assert got.provenance is Provenance.DERIVED
    assert got.lifecycle is BlockLifecycle.PERSISTENT


def test_blocks_ordered_by_ord(store):
    f = Feature(title="X")
    store.upsert_feature(f)
    store.upsert_block(Block(feature_id=f.id, kind="prose", ord=2, content="second"))
    store.upsert_block(Block(feature_id=f.id, kind="diagram", ord=0, content="first"))
    store.upsert_block(Block(feature_id=f.id, kind="image", ord=1, content="middle"))
    contents = [b.content for b in store.blocks_for_feature(f.id)]
    assert contents == ["first", "middle", "second"]


def test_upsert_same_id_preserves_identity_on_move(store):
    f = Feature(title="X")
    store.upsert_feature(f)
    b = Block(feature_id=f.id, kind="image", content="ref", ord=0)
    store.upsert_block(b)
    # move it (ord change) — same id upserted
    store.upsert_block(b.model_copy(update={"ord": 5}))
    rows = store.blocks_for_feature(f.id)
    assert len(rows) == 1            # not duplicated
    assert rows[0].id == b.id
    assert rows[0].ord == 5


def test_delete_block(store):
    f = Feature(title="X")
    store.upsert_feature(f)
    b = Block(feature_id=f.id, kind="latex", content=r"\sum")
    store.upsert_block(b)
    store.delete_block(b.id)
    assert store.get_block(b.id) is None


def test_blocks_for_features_batch(store):
    f1, f2 = Feature(title="A"), Feature(title="B")
    store.upsert_feature(f1)
    store.upsert_feature(f2)
    store.upsert_block(Block(feature_id=f1.id, kind="diagram"))
    store.upsert_block(Block(feature_id=f2.id, kind="image"))
    got = store.blocks_for_features({f1.id, f2.id})
    assert len({b.feature_id for b in got}) == 2


def test_existing_feature_has_zero_blocks(store):
    # back-compat: a feature with no typed media owns no block rows.
    f = Feature(title="legacy")
    store.upsert_feature(f)
    assert store.blocks_for_feature(f.id) == []


def test_binding_uniqueness_invariant_untouched(store):
    """KTD1: blocks do NOT relax UNIQUE(file, symbol_path). A second feature binding
    an already-bound chunk still re-attributes (one owner), even with blocks present."""
    f1, f2 = Feature(title="A"), Feature(title="B")
    store.upsert_feature(f1)
    store.upsert_feature(f2)
    # a diagram block on each feature, both notionally "about" the same code
    store.upsert_block(Block(feature_id=f1.id, kind="diagram"))
    store.upsert_block(Block(feature_id=f2.id, kind="diagram"))
    store.upsert_binding(Binding(feature_id=f1.id, file="a.py", symbol_path="a.py::foo", fingerprint="h1"))
    # binding the same anchor to f2 re-attributes it; there is still exactly one row
    store.upsert_binding(Binding(feature_id=f2.id, file="a.py", symbol_path="a.py::foo", fingerprint="h2"))
    owner = store.binding_at("a.py", "a.py::foo")
    assert owner.feature_id == f2.id
    assert len(store.all_bindings()) == 1
