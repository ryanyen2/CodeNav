"""U3 — Loop B block `lower` dispatch: a diagram edit queues a scoped directive,
a remove drops only the projection (never code), an ambiguous edit is held as a
draft, and a move/ord-only change is a no-op."""
from __future__ import annotations

from pathlib import Path

import pytest

from codoc.loop import edits as edits_channel
from codoc.loop.edits import BlockEdit, append_block_edit
from codoc.loop.loop_b import realize_path, run_loop_b
from codoc.model.binding import Binding
from codoc.model.block import Block
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def repo(tmp_path):
    """A repo with a bound feature carrying a diagram block."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    with open_store(codoc_dir) as s:
        f = Feature(title="Auth")
        s.upsert_feature(f)
        s.upsert_binding(Binding(feature_id=f.id, file="a.py",
                                 symbol_path="a.py::login", fingerprint="h"))
        blk = Block(feature_id=f.id, kind="diagram",
                    content="flowchart TB\n  login --> make_token")
        s.upsert_block(blk)
    return str(tmp_path), str(codoc_dir), f.id, blk.id


def test_diagram_edit_queues_directive(repo):
    root, codoc_dir, fid, bid = repo
    append_block_edit(codoc_dir, BlockEdit(
        block_id=bid, feature_id=fid, kind="diagram", action="edit",
        prev_content="flowchart TB\n  login --> make_token",
        content="flowchart TB\n  login"))  # removed the edge
    res = run_loop_b(root, codoc_dir)
    assert res.queued
    realize = realize_path(codoc_dir).read_text()
    assert "BLOCK EDIT [diagram]" in realize
    assert "Remove the dependency" in realize
    # the block edit was drained one-shot
    assert edits_channel.read_block_edits(codoc_dir) == []


def test_remove_drops_projection_not_code(repo):
    root, codoc_dir, fid, bid = repo
    append_block_edit(codoc_dir, BlockEdit(
        block_id=bid, feature_id=fid, kind="diagram", action="remove"))
    res = run_loop_b(root, codoc_dir)
    # the block row is gone (projection dropped)...
    with open_store(codoc_dir) as s:
        assert s.get_block(bid) is None
        # ...but the binding (the code attribution) is untouched (KTD2)
        assert len(s.bindings_for_feature(fid)) == 1
    # and NO realize directive was queued for the removal
    assert not res.queued
    assert not realize_path(codoc_dir).exists()


def test_ambiguous_edit_is_held_draft(repo):
    root, codoc_dir, fid, bid = repo
    append_block_edit(codoc_dir, BlockEdit(
        block_id=bid, feature_id=fid, kind="diagram", action="edit",
        prev_content="flowchart TB\n  login --> make_token",
        content="flowchart TB\n  login --> make_token\n  Z[freeform]"))
    res = run_loop_b(root, codoc_dir)
    # a draft is held: it's in the manifest but NOT handed off to realize.md
    assert not realize_path(codoc_dir).exists()
    manifest = edits_channel.read_manifest(codoc_dir)
    assert any(not d.handed_off and d.feature_id == fid for d in manifest)


def test_consult_only_kind_does_not_lower(repo):
    root, codoc_dir, fid, _bid = repo
    # a url block is consult-only — editing it implies no code change
    with open_store(codoc_dir) as s:
        url = Block(feature_id=fid, kind="url", content="https://old.example")
        s.upsert_block(url)
    append_block_edit(codoc_dir, BlockEdit(
        block_id=url.id, feature_id=fid, kind="url", action="edit",
        content="https://new.example"))
    res = run_loop_b(root, codoc_dir)
    assert not res.queued
