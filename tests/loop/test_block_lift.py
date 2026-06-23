"""U3 — Loop A block `lift`: a diagram refreshes from changed code, and doc-wins
means a block on a held feature is never clobbered by a refresh."""
from __future__ import annotations

import pytest

from codoc.blocks.refresh import refresh_lift_blocks
from codoc.loop import edits as edits_channel
from codoc.model.binding import Binding
from codoc.model.block import Block, Provenance
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    (tmp_path / ".codoc").mkdir()
    s = open_store(tmp_path / ".codoc")
    yield s
    s.close()


def _edge(store, src, dst):
    store.insert_edges([{
        "src_file": "a.py", "src_symbol": src, "dst_name": dst.split("::")[-1],
        "dst_symbol": dst, "dst_file": "a.py", "kind": "call", "internal": 1,
    }])


def test_lift_refreshes_stale_diagram(tmp_path, store):
    f = Feature(title="Auth")
    store.upsert_feature(f)
    store.upsert_binding(Binding(feature_id=f.id, file="a.py",
                                 symbol_path="a.py::login", fingerprint="h"))
    _edge(store, "a.py::login", "a.py::make_token")
    blk = Block(feature_id=f.id, kind="diagram", content="flowchart TB\n  stale")
    store.upsert_block(blk)

    changed = refresh_lift_blocks(store, str(tmp_path / ".codoc"))
    assert changed == 1
    refreshed = store.get_block(blk.id)
    assert "login --> make_token" in refreshed.content
    assert refreshed.provenance is Provenance.DERIVED
    # idempotent: a second pass with no code change refreshes nothing
    assert refresh_lift_blocks(store, str(tmp_path / ".codoc")) == 0


def test_lift_skips_held_feature(tmp_path, store):
    codoc_dir = str(tmp_path / ".codoc")
    f = Feature(title="Auth")
    store.upsert_feature(f)
    store.upsert_binding(Binding(feature_id=f.id, file="a.py",
                                 symbol_path="a.py::login", fingerprint="h"))
    _edge(store, "a.py::login", "a.py::make_token")
    blk = Block(feature_id=f.id, kind="diagram", content="flowchart TB\n  my-edit-in-progress")
    store.upsert_block(blk)
    # hold the feature with a draft directive (doc-ahead intent pending)
    edits_channel.write_manifest(codoc_dir, [edits_channel.Directive(
        id="d-1", feature_id=f.id, kind="diagram", text="x", handed_off=False)])

    changed = refresh_lift_blocks(store, codoc_dir)
    assert changed == 0  # doc-wins: the human's pending edit is not clobbered
    assert store.get_block(blk.id).content == "flowchart TB\n  my-edit-in-progress"
