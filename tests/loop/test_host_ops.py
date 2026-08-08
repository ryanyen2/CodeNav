"""#2 — the IDE→daemon host-op append log (edits.host.jsonl) + daemon merge.

The IDE is a separate process that does not hold the edits.json cross-process lock, so
it must never read-modify-write edits.json (a lost command / hand-off / steer, or a
fixed-tmp ENOENT crash). Instead it APPENDS one op per line to edits.host.jsonl, and the
daemon MERGES those ops into edits.json under the lock at the start of every Loop B pass.
These tests pin the merge: recognised ops apply through the existing writers, order is
preserved, one garbled line doesn't block the rest, and a crash-orphaned .merging file is
recovered.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from codoc.codoc_file.render import write_tree
from codoc.loop import edits as ec
from codoc.loop.edits import append_host_op, host_ops_path, merge_host_ops
from codoc.loop.loop_b import run_loop_b
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def dirs(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    codoc_dir = tmp_path / ".codoc"; codoc_dir.mkdir()
    return str(root), str(codoc_dir)


def test_merge_applies_command_and_consumes_log(dirs):
    _, codoc_dir = dirs
    append_host_op(codoc_dir, "appendCommand", {
        "id": "c-1", "kind": "add", "local_id": "L1",
        "payload": {"title": "Theme", "description": "x"}})

    n = merge_host_ops(codoc_dir)

    assert n == 1
    assert not host_ops_path(codoc_dir).exists()          # log consumed
    cmds = ec.read_commands(codoc_dir)                     # merged into edits.json
    assert [c.id for c in cmds] == ["c-1"] and cmds[0].kind == "add"


def test_merge_preserves_order_and_all_op_kinds(dirs):
    _, codoc_dir = dirs
    append_host_op(codoc_dir, "appendCommand", {"id": "c-1", "kind": "add", "payload": {"title": "A"}})
    append_host_op(codoc_dir, "appendSteer", {"feature_id": "f-1", "text": "note", "comment_id": "t-1"})
    append_host_op(codoc_dir, "setDrafts", ["f-1", "f-2"])
    append_host_op(codoc_dir, "appendHandoffs", ["f-3"])
    append_host_op(codoc_dir, "appendCancellation", {"feature_id": "f-4"})

    assert merge_host_ops(codoc_dir) == 5
    assert [c.id for c in ec.read_commands(codoc_dir)] == ["c-1"]
    assert [s.comment_id for s in ec.read_steers(codoc_dir)] == ["t-1"]
    assert ec.read_drafts(codoc_dir) == {"f-1", "f-2"}
    assert ec.read_handoffs(codoc_dir) == ["f-3"]
    assert ec.read_cancellations(codoc_dir) == ["f-4"]


def test_merge_skips_garbled_line_but_applies_the_rest(dirs):
    _, codoc_dir = dirs
    p = host_ops_path(codoc_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        '{"fn": "appendCommand", "arg": {"id": "c-1", "kind": "add", "payload": {"title": "A"}}}\n'
        'this is not json\n'
        '{"fn": "unknownFutureOp", "arg": {}}\n'
        '{"fn": "appendCommand", "arg": {"id": "c-2", "kind": "add", "payload": {"title": "B"}}}\n'
    )
    n = merge_host_ops(codoc_dir)
    assert n == 2  # the two valid commands; garbled + unknown skipped
    assert [c.id for c in ec.read_commands(codoc_dir)] == ["c-1", "c-2"]


def test_merge_is_noop_without_a_log(dirs):
    _, codoc_dir = dirs
    assert merge_host_ops(codoc_dir) == 0


def test_merge_recovers_crash_orphaned_merging_file(dirs):
    """A .merging file left by a crashed merge is drained on the next merge."""
    _, codoc_dir = dirs
    merging = Path(str(host_ops_path(codoc_dir)) + ".merging")
    merging.parent.mkdir(parents=True, exist_ok=True)
    merging.write_text('{"fn": "appendCommand", "arg": {"id": "c-x", "kind": "add", "payload": {"title": "X"}}}\n')
    n = merge_host_ops(codoc_dir)
    assert n == 1
    assert not merging.exists()
    assert [c.id for c in ec.read_commands(codoc_dir)] == ["c-x"]


def test_run_loop_b_absorbs_host_ops_end_to_end(dirs):
    """A command that only ever reached edits.host.jsonl (never edits.json) is applied by
    Loop B — the frozen-editor race fix (#2)."""
    root, codoc_dir = dirs
    with open_store(codoc_dir) as s:  # empty tree seed
        write_tree(s, codoc_dir)
    append_host_op(codoc_dir, "appendCommand", {
        "id": "c-live", "kind": "add", "local_id": "L9",
        "payload": {"title": "Live feature", "description": "y"}})

    res = run_loop_b(root, codoc_dir, realize=True)

    assert res.commands == 1
    with open_store(codoc_dir) as s:
        assert any(f.title == "Live feature" for f in s.list_features())
    assert not host_ops_path(codoc_dir).exists()  # consumed

def test_merge_preserves_base_text_and_session(dirs):
    """The merge-claim fields ride the host-op round trip. Dropping them here
    silently disabled the whole 3-way merge for every real IDE command
    (base_text=None -> CLEAN -> blind last-writer-wins in both directions)."""
    _, codoc_dir = dirs
    append_host_op(codoc_dir, "appendCommand", {
        "id": "c-bt", "kind": "set_description", "feature_id": "f-1",
        "base_text": "old text", "session": "sess-9",
        "payload": {"description": "new text"}})
    assert merge_host_ops(codoc_dir) == 1
    (cmd,) = ec.read_commands(codoc_dir)
    assert cmd.base_text == "old text"
    assert cmd.session == "sess-9"


def test_merge_keeps_an_empty_base_text_claim(dirs):
    """'' is a real claim (the field was empty when the author started), distinct
    from None (no claim at all) — `or`-style coalescing would erase it."""
    _, codoc_dir = dirs
    append_host_op(codoc_dir, "appendCommand", {
        "id": "c-bt2", "kind": "set_description", "feature_id": "f-1",
        "base_text": "", "payload": {"description": "text"}})
    assert merge_host_ops(codoc_dir) == 1
    (cmd,) = ec.read_commands(codoc_dir)
    assert cmd.base_text == ""
