"""Atomic control-file IO (`codoc/workspace/layout.py`).

The key property under test is the one that bit a user in the field: two writers of
the same control file (two `codoc watch` daemons, or a daemon racing an MCP reflection)
must NOT collide on a shared tmp name and crash on `os.replace` when the other already
renamed it. The fix is a per-writer-unique tmp; these tests pin the observable contract
(correct content, no orphaned tmp, safe under repeated/overlapping writes, cleanup on
failure)."""
from __future__ import annotations

import json

import pytest

from codoc.workspace import layout


def _tmps(d):
    return sorted(p.name for p in d.iterdir() if p.name.endswith(".tmp"))


def test_atomic_write_writes_and_leaves_no_tmp(tmp_path):
    dest = tmp_path / "status.json"
    layout.atomic_write(dest, "hello")
    assert dest.read_text() == "hello"
    assert _tmps(tmp_path) == []  # the unique tmp was renamed away, none orphaned


def test_write_json_roundtrips(tmp_path):
    dest = tmp_path / "status.json"
    layout.write_json(dest, {"state": "synced", "pending": ["b-1"]})
    assert json.loads(dest.read_text()) == {"state": "synced", "pending": ["b-1"]}
    assert _tmps(tmp_path) == []


def test_creates_missing_parent(tmp_path):
    dest = tmp_path / "nested" / "deep" / "out.json"
    layout.write_json(dest, {"ok": True})
    assert json.loads(dest.read_text()) == {"ok": True}


def test_repeated_writes_never_collide_on_tmp(tmp_path):
    """Each write uses its own tmp file, so a second writer never finds a
    shared tmp already renamed. Many writes in a row each succeed and leave no
    tmp behind (serialized here for determinism)."""
    dest = tmp_path / "status.json"
    for i in range(20):
        layout.write_json(dest, {"n": i})
    assert json.loads(dest.read_text()) == {"n": 19}
    assert _tmps(tmp_path) == []  # no orphaned tmp from any write


def test_serialization_error_writes_nothing(tmp_path):
    dest = tmp_path / "out.json"
    # json.dumps raises before any tmp is created → no tmp, no dest, error propagates.
    with pytest.raises(TypeError):
        layout.write_json(dest, {"bad": object()})
    assert _tmps(tmp_path) == []
    assert not dest.exists()


def test_failed_replace_cleans_up_tmp(tmp_path):
    # dest is an existing DIRECTORY → os.replace(tmp, dest) raises. This exercises the
    # except branch: the unique tmp must be unlinked, not orphaned, and the error re-raised.
    dest = tmp_path / "adir"
    dest.mkdir()
    with pytest.raises(OSError):
        layout.atomic_write(dest, "x")
    assert _tmps(tmp_path) == []


def test_read_json_quarantines_a_corrupt_nonempty_file(tmp_path):
    """A non-empty file that won't parse must be moved aside (not silently treated as
    empty) so the next writer, which merges over ``read_json→{}``, can't overwrite and
    drop its un-drained contents. The data survives in a ``.corrupt-*`` sibling."""
    dest = tmp_path / "offsets.json"
    dest.write_text('{"ide": [ truncated…')  # invalid JSON, non-empty
    assert layout.read_json(dest, default={}) == {}
    assert not dest.exists()                       # moved aside, not left in place
    corrupt = list(tmp_path.glob("offsets.json.corrupt-*"))
    assert len(corrupt) == 1
    assert "truncated" in corrupt[0].read_text()   # original bytes preserved


def test_read_json_missing_and_empty_do_not_quarantine(tmp_path):
    missing = tmp_path / "nope.json"
    assert layout.read_json(missing, default={"d": 1}) == {"d": 1}
    empty = tmp_path / "empty.json"
    empty.write_text("   \n")                       # whitespace only → not corruption
    assert layout.read_json(empty, default={}) == {}
    assert empty.exists()                           # left alone
    assert list(tmp_path.glob("*.corrupt-*")) == []
