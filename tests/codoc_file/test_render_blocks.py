"""U2 — the v6 sidecar `blocks` slice: persistent blocks only, ordered, back-compat."""
from __future__ import annotations

import json

import pytest

from codoc.codoc_file.render import BINDINGS_FILENAME, write_sidecar
from codoc.model.block import Block, BlockLifecycle
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _sidecar(tmp_path):
    return json.loads((tmp_path / BINDINGS_FILENAME).read_text())


def test_sidecar_version_bumped_to_6(store, tmp_path):
    write_sidecar(store, tmp_path)
    assert _sidecar(tmp_path)["version"] == 6


def test_blocks_slice_carries_persistent_blocks_ordered(store, tmp_path):
    f = Feature(title="Auth")
    store.upsert_feature(f)
    store.upsert_block(Block(feature_id=f.id, kind="image", content="b", ord=1))
    store.upsert_block(Block(feature_id=f.id, kind="diagram", content="a", ord=0))
    write_sidecar(store, tmp_path)
    blocks = _sidecar(tmp_path)["blocks"]
    assert [b["content"] for b in blocks[f.id]] == ["a", "b"]
    assert blocks[f.id][0]["kind"] == "diagram"
    assert "id" in blocks[f.id][0] and blocks[f.id][0]["id"].startswith("blk-")


def test_transient_blocks_excluded_from_sidecar(store, tmp_path):
    f = Feature(title="Auth")
    store.upsert_feature(f)
    store.upsert_block(Block(feature_id=f.id, kind="screenshot", content="bug.png",
                             lifecycle=BlockLifecycle.TRANSIENT))
    write_sidecar(store, tmp_path)
    # transient blocks ride the steers channel; they are NOT durable sidecar rows
    assert _sidecar(tmp_path)["blocks"].get(f.id) in (None, [])


def test_feature_with_no_blocks_absent_from_slice(store, tmp_path):
    f = Feature(title="Plain")
    store.upsert_feature(f)
    write_sidecar(store, tmp_path)
    sc = _sidecar(tmp_path)
    assert "blocks" in sc                # slice always present (presence-keyed)
    assert f.id not in sc["blocks"]      # but a block-less feature isn't listed
