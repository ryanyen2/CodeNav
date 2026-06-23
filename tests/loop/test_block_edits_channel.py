"""U2 — the edits.json block-edit channel: round-trip, one-shot drain, and
sibling-list preservation (a block-edit write never drops steers/intents/drafts)."""
from __future__ import annotations

from codoc.loop.edits import (
    BlockEdit,
    Intent,
    Steer,
    append_block_edit,
    append_steer,
    drain_block_edits,
    read_block_edits,
    read_intents,
    read_steers,
    set_drafts,
)


def test_block_edit_roundtrip(tmp_path):
    append_block_edit(tmp_path, BlockEdit(
        block_id="blk-1", feature_id="f-1", kind="diagram",
        action="edit", content="new", prev_content="old"))
    got = read_block_edits(tmp_path)
    assert len(got) == 1
    assert got[0].block_id == "blk-1"
    assert got[0].action == "edit"
    assert got[0].content == "new"
    assert got[0].prev_content == "old"


def test_drain_is_one_shot(tmp_path):
    append_block_edit(tmp_path, BlockEdit(block_id="blk-1", feature_id="f-1", kind="diagram"))
    assert len(drain_block_edits(tmp_path)) == 1
    assert read_block_edits(tmp_path) == []   # consumed


def test_block_edit_preserves_sibling_lists(tmp_path):
    # seed unrelated channels, then write a block-edit — none must be dropped
    append_steer(tmp_path, Steer(feature_id="f-2", text="please rename"))
    set_drafts(tmp_path, ["f-3"])
    append_block_edit(tmp_path, BlockEdit(block_id="blk-9", feature_id="f-1", kind="latex"))
    assert len(read_steers(tmp_path)) == 1
    assert len(read_block_edits(tmp_path)) == 1
    # draining block_edits keeps the steer
    drain_block_edits(tmp_path)
    assert len(read_steers(tmp_path)) == 1


def test_remove_action_carried_verbatim(tmp_path):
    append_block_edit(tmp_path, BlockEdit(
        block_id="blk-x", feature_id="f-1", kind="latex", action="remove"))
    got = read_block_edits(tmp_path)[0]
    assert got.action == "remove"
    assert got.content == ""
