"""Robustness: the user's real editing patterns survive the loop intact.

These pin the KTD8 promise — identity is structural, only transformation is the
LLM — against the messy ways people actually edit (revert-to-baseline, remove a
block, edits that are really a delete + retype). The loop diffs settled state
against a baseline, so none of these mis-fire a code change."""
from __future__ import annotations

import pytest

from codoc.loop import edits as edits_channel
from codoc.loop.edits import BlockEdit, append_block_edit
from codoc.loop.loop_b import realize_path, run_loop_b
from codoc.model.binding import Binding
from codoc.model.block import Block
from codoc.model.feature import Feature
from codoc.store.db import open_store

DIAGRAM = "flowchart TB\n  login --> make_token"


@pytest.fixture
def repo(tmp_path):
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    with open_store(codoc_dir) as s:
        f = Feature(title="Auth")
        s.upsert_feature(f)
        s.upsert_binding(Binding(feature_id=f.id, file="a.py",
                                 symbol_path="a.py::login", fingerprint="h"))
        blk = Block(feature_id=f.id, kind="diagram", content=DIAGRAM)
        s.upsert_block(blk)
    return str(tmp_path), str(codoc_dir), f.id, blk.id


def test_edit_then_revert_to_baseline_is_a_noop(repo):
    """User changes the diagram then undoes — final content equals the original, so
    the deterministic edge diff sees no change and queues nothing."""
    root, codoc_dir, fid, bid = repo
    append_block_edit(codoc_dir, BlockEdit(
        block_id=bid, feature_id=fid, kind="diagram", action="edit",
        prev_content=DIAGRAM, content=DIAGRAM))  # net-zero (host supersede kept final state)
    res = run_loop_b(root, codoc_dir)
    assert not res.queued
    assert not realize_path(codoc_dir).exists()


def test_remove_latex_block_keeps_code(tmp_path):
    """Removing a LaTeX block drops the projection but never the code (KTD2)."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    with open_store(codoc_dir) as s:
        f = Feature(title="Stats")
        s.upsert_feature(f)
        s.upsert_binding(Binding(feature_id=f.id, file="s.py",
                                 symbol_path="s.py::mean", fingerprint="h"))
        latex = Block(feature_id=f.id, kind="latex", content=r"\bar{x} = \frac{1}{n}\sum x_i")
        s.upsert_block(latex)
    append_block_edit(str(codoc_dir), BlockEdit(
        block_id=latex.id, feature_id=f.id, kind="latex", action="remove"))
    res = run_loop_b(str(tmp_path), str(codoc_dir))
    with open_store(codoc_dir) as s:
        assert s.get_block(latex.id) is None              # projection gone
        assert len(s.bindings_for_feature(f.id)) == 1     # code attribution kept
    assert not res.queued


def test_bundled_delete_and_retype_diffs_by_content_not_keystrokes(repo):
    """An edit that internally deleted the old edge line and retyped a different one
    arrives as a content delta; the deterministic differ reads the net edge change,
    not the intermediate deletion."""
    root, codoc_dir, fid, bid = repo
    append_block_edit(codoc_dir, BlockEdit(
        block_id=bid, feature_id=fid, kind="diagram", action="edit",
        prev_content=DIAGRAM,
        content="flowchart TB\n  login --> audit"))  # make_token edge → audit edge
    res = run_loop_b(root, codoc_dir)
    assert res.queued
    body = realize_path(codoc_dir).read_text()
    assert "Remove the dependency" in body and "make_token" in body  # old edge removed
    assert "Add a dependency" in body and "audit" in body            # new edge added


def test_no_block_edit_means_no_block_activity(repo):
    """A move (ord-only change) emits no block_edit from the host, so a pass with no
    block_edits touches no block and queues nothing."""
    root, codoc_dir, fid, bid = repo
    assert edits_channel.read_block_edits(codoc_dir) == []
    res = run_loop_b(root, codoc_dir)
    assert not res.queued
    with open_store(codoc_dir) as s:
        assert s.get_block(bid).content == DIAGRAM  # untouched
