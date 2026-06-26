"""U8 — the one-time, idempotent store-authoritative migration.

Covers the two heals in :mod:`codoc.loop.migrate`:

- duplicate-feature dedup converges a diverged group (one binding-owner + several
  binding-less re-mint husks) onto the binding-owner with no content/binding loss,
  re-pointing marks/comments, and a subsequent ``run_loop_b`` mints nothing new;
- comment migration lifts ``tree.doc.json`` ``DocFile.comments`` into the store
  ``comments`` table and is idempotent on re-run;
- a clean workspace is a no-op.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codoc.loop.filenames import DOC_FILENAME
from codoc.loop.loop_b import run_loop_b
from codoc.loop.migrate import migrate_workspace
from codoc.model.annotation import CommentThread, Mark, MarkKind
from codoc.model.binding import Binding
from codoc.model.block import Provenance
from codoc.model.feature import Feature
from codoc.model.hlc import HLC
from codoc.store.db import open_store


@pytest.fixture
def dirs(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    return str(root), str(codoc_dir)


def _write_doc_comments(codoc_dir, comments: list[dict]) -> None:
    """Write a minimal pre-refactor tree.doc.json DocFile wrapper carrying
    host-authored comment threads."""
    doc = {"type": "doc", "content": []}
    path = Path(codoc_dir) / DOC_FILENAME
    path.write_text(json.dumps({"version": 1, "doc": doc, "suggestions": [], "comments": comments}))


# ── dedup: converge onto the binding-owner, no content/binding loss ──────────
def test_dedup_converges_onto_binding_owner(dirs):
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    # Keeper holds the binding; it has a SHORT description.
    keeper = Feature(title="Other Local Agents Connection", description="short", parent_id=None)
    s.upsert_feature(keeper)
    s.upsert_binding(Binding(
        feature_id=keeper.id, file="agents.py", symbol_path="agents.py::connect",
        fingerprint="fp"))
    # Two binding-less re-mint husks of the SAME (title, parent). One carries a
    # longer (author-edited) description that should merge onto the keeper.
    husk_a = Feature(title="Other Local Agents Connection",
                     description="A much longer, edited description of the connection.",
                     parent_id=None)
    husk_b = Feature(title="other local agents connection ",  # normalizes to same key
                     description="", parent_id=None)
    s.upsert_feature(husk_a)
    s.upsert_feature(husk_b)
    # A mark + comment on a husk must re-point to the keeper.
    s.upsert_mark(Mark(feature_id=husk_a.id, kind=MarkKind.AMEND, anchor_start=0, anchor_end=3))
    s.upsert_comment(CommentThread(feature_id=husk_b.id, body="please fix"))
    s.close()

    res = migrate_workspace(codoc_dir)
    assert res.duplicate_groups == 1
    assert res.features_retired == 2

    s = open_store(codoc_dir)
    live = s.list_features()
    # Only the keeper survives in the group.
    assert [f.id for f in live] == [keeper.id]
    survivor = s.get_feature(keeper.id)
    # Binding preserved.
    assert len(s.bindings_for_feature(keeper.id)) == 1
    # Longer husk description merged (keeper's was shorter), not clobbered to empty.
    assert survivor.description == "A much longer, edited description of the connection."
    # Marks + comments re-pointed.
    assert len(s.marks_for_feature(keeper.id)) == 1
    assert len(s.comments_for_feature(keeper.id)) == 1
    s.close()

    # A subsequent Loop B pass mints nothing new (the collision is gone).
    res_b = run_loop_b(root, codoc_dir, dry_run=False)
    s = open_store(codoc_dir)
    assert len([f for f in s.list_features() if "other local agents" in f.title.lower()]) == 1
    s.close()


def test_dedup_keeps_keeper_longer_description(dirs):
    """The keeper's own LONGER description must not be clobbered by a shorter husk."""
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    keeper = Feature(title="Auth", description="The full, considered description of auth.")
    s.upsert_feature(keeper)
    s.upsert_binding(Binding(feature_id=keeper.id, file="a.py", symbol_path="a.py::x", fingerprint="f"))
    husk = Feature(title="Auth", description="short")
    s.upsert_feature(husk)
    s.close()

    migrate_workspace(codoc_dir)
    s = open_store(codoc_dir)
    assert s.get_feature(keeper.id).description == "The full, considered description of auth."
    s.close()


def test_dedup_tiebreak_no_bindings_keeps_earliest(dirs):
    """No duplicate holds bindings → keep earliest created_at."""
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    early = Feature(title="Theme", created_at=HLC(wall_clock=1000, logical_time=0, node_id="n"))
    late = Feature(title="Theme", created_at=HLC(wall_clock=2000, logical_time=0, node_id="n"))
    s.upsert_feature(early)
    s.upsert_feature(late)
    s.close()

    res = migrate_workspace(codoc_dir)
    assert res.features_retired == 1
    s = open_store(codoc_dir)
    assert [f.id for f in s.list_features()] == [early.id]
    s.close()


# ── comment migration: lands threads in store, idempotent ────────────────────
def test_comment_migration_lands_threads_and_is_idempotent(dirs):
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    f = Feature(title="Auth")
    s.upsert_feature(f)
    s.close()

    _write_doc_comments(codoc_dir, [
        {"id": "cm-1", "featureId": f.id, "body": "fix this", "author": "human", "status": "open"},
        {"id": "cm-2", "featureId": f.id, "body": "and this", "author": "human", "status": "sent"},
        # Null-fid held thread (never anchored) — skipped, not lost-with-crash.
        {"id": "cm-3", "featureId": None, "body": "held", "status": "open"},
    ])

    res = migrate_workspace(codoc_dir)
    assert res.comments_migrated == 2

    s = open_store(codoc_dir)
    got = {c.id: c for c in s.comments_for_feature(f.id)}
    assert set(got) == {"cm-1", "cm-2"}
    assert got["cm-1"].body == "fix this"
    s.close()

    # Re-run: idempotent (no duplicate threads).
    res2 = migrate_workspace(codoc_dir)
    assert res2.comments_migrated == 0
    # cm-1 + cm-2 already present, cm-3 null-fid (no durable home) → all skipped.
    assert res2.comments_skipped == 3
    s = open_store(codoc_dir)
    assert len(s.all_comments()) == 2
    s.close()


def test_comment_migration_noop_when_daemon_rebuilt(dirs):
    """A daemon-rebuilt tree.doc.json (no comments key) is a no-op."""
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    s.upsert_feature(Feature(title="Auth"))
    s.close()
    (Path(codoc_dir) / DOC_FILENAME).write_text(json.dumps({"type": "doc", "content": []}))

    res = migrate_workspace(codoc_dir)
    assert res.comments_migrated == 0
    s = open_store(codoc_dir)
    assert s.all_comments() == []
    s.close()


# ── clean workspace: no-op ───────────────────────────────────────────────────
def test_clean_workspace_is_noop(dirs):
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    s.upsert_feature(Feature(title="One"))
    s.upsert_feature(Feature(title="Two"))
    s.close()

    res = migrate_workspace(codoc_dir)
    assert not res.changed()
    assert res.duplicate_groups == 0
    assert res.features_retired == 0
    assert res.comments_migrated == 0
