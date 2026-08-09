"""P-6 — the realize queue never outlives the feature it names.

A command-driven retire supersedes that feature's queued directives, but that write
happens outside the transaction that applied the retire and ledgered its command. A crash
in between leaves the retire applied, the command permanently ledgered (so nothing
replays) and the directive queued forever — the agent is eventually asked to implement
prose for a feature that is no longer in the tree. The invariant is therefore re-derived
once per pass instead of being maintained only at the call site.
"""
from __future__ import annotations

import pytest

from codoc.codoc_file.render import write_tree
from codoc.loop import edits as edits_channel
from codoc.loop.loop_b import realize_path, run_loop_b
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    codoc_dir = tmp_path / ".codoc"; codoc_dir.mkdir()
    return str(root), str(codoc_dir)


def _seed(codoc_dir, **kw):
    with open_store(codoc_dir) as s:
        f = Feature(title="Feat", description="seed.", **kw)
        s.upsert_feature(f)
        write_tree(s, codoc_dir)
    return f


def _queue(codoc_dir, fid, *, kind="amend", handed_off=False, ts=1, did=None):
    did = did or f"d-{'0' * 6}{'1' if kind == 'amend' else '2'}"
    edits_channel.write_manifest(codoc_dir, [edits_channel.Directive(
        id=did, feature_id=fid, kind=kind, caused_by="c-1",
        text=f"Implement {kind} for {fid}", baseline="seed.",
        handed_off=handed_off, ts=ts)])
    return did


def _trigger(codoc_dir, did):
    """Write the agent trigger the way build_realize_prompt does — the id marker is what
    `_in_flight_directive_ids` reads, so a hand-crafted file has to carry it."""
    realize_path(codoc_dir).write_text(f"## 1. Do the thing \u27e8{did}\u27e9\n")


def _retire(codoc_dir, fid):
    with open_store(codoc_dir) as s:
        s.retire_feature(fid)


def test_a_directive_left_by_a_torn_retire_is_dropped_on_the_next_pass(repo):
    root, codoc_dir = repo
    f = _seed(codoc_dir)
    _queue(codoc_dir, f.id)          # the directive the supersede never got to drop
    _retire(codoc_dir, f.id)         # …because the process died right here

    run_loop_b(root, codoc_dir, dry_run=False)

    assert edits_channel.read_manifest(codoc_dir) == []
    assert not realize_path(codoc_dir).exists()


def test_a_retire_directive_for_a_retired_feature_survives(repo):
    """The destructive `~`: the whole point of a retire directive is to ask the agent to
    delete the code for a feature that IS retired."""
    root, codoc_dir = repo
    f = _seed(codoc_dir)
    did = _queue(codoc_dir, f.id, kind="retire", handed_off=True)
    _trigger(codoc_dir, did)
    _retire(codoc_dir, f.id)

    run_loop_b(root, codoc_dir, dry_run=False)

    assert [d.kind for d in edits_channel.read_manifest(codoc_dir)] == ["retire"]


def test_a_directive_for_a_deleted_feature_is_dropped(repo):
    root, codoc_dir = repo
    _seed(codoc_dir)
    _queue(codoc_dir, "f-never-existed")

    run_loop_b(root, codoc_dir, dry_run=False)

    assert edits_channel.read_manifest(codoc_dir) == []


def test_a_live_feature_keeps_its_queued_directive(repo):
    root, codoc_dir = repo
    f = _seed(codoc_dir)
    _queue(codoc_dir, f.id)

    run_loop_b(root, codoc_dir, dry_run=False)

    assert [d.feature_id for d in edits_channel.read_manifest(codoc_dir)] == [f.id]


def test_an_in_flight_directive_is_protected_even_for_a_retired_feature(repo):
    """INV8: the agent may be mid-implementation and its reflect call cites the directive
    id, so anything already in realize.md is left alone (same carve-out as supersede)."""
    root, codoc_dir = repo
    f = _seed(codoc_dir)
    did = _queue(codoc_dir, f.id, handed_off=True)
    _trigger(codoc_dir, did)
    _retire(codoc_dir, f.id)

    run_loop_b(root, codoc_dir, dry_run=False)

    assert [d.id for d in edits_channel.read_manifest(codoc_dir)] == [did]
