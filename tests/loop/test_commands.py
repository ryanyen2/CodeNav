"""U3 — identity-keyed command channel + applier (KTD3 / KTD8 / KTD10).

Authored edits arrive as EXPLICIT commands (add/set_title/set_description/move/
retire) keyed by feature/local id and applied via ``apply_op`` — NOT inferred from
a doc diff. These tests pin the applier's contract: minted-fid correlation,
idempotency on the store ledger, the ``(normalized_title, parent_id)`` dedup guard,
structural retire/move, in-place amend, drain-order (commands before legacy
annotations), and the vanished-target skip.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from codoc.codoc_file.render import tree_path, write_tree
from codoc.loop import edits as edits_channel
from codoc.loop.edits import Command, append_command
from codoc.loop.loop_b import run_loop_b
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def dirs(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    return str(root), str(codoc_dir)


def _seed_tree(codoc_dir, *features: Feature) -> None:
    s = open_store(codoc_dir)
    for f in features:
        s.upsert_feature(f)
    write_tree(s, codoc_dir)
    s.close()


# ── add: mints exactly one feature + echoes fid keyed to local_id ────────────
def test_add_mints_one_feature_and_echoes_fid_by_local_id(dirs):
    root, codoc_dir = dirs
    _seed_tree(codoc_dir)  # empty tree
    append_command(codoc_dir, Command(
        id="cmd-1", kind="add", local_id="local-42",
        payload={"title": "Theme system", "description": "Light/dark switcher."}))

    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.commands == 1
    s = open_store(codoc_dir)
    minted = [f for f in s.list_features() if f.title == "Theme system"]
    assert len(minted) == 1
    fid = minted[0].id
    assert minted[0].description == "Light/dark switcher."
    assert minted[0].local_id == "local-42"
    s.close()
    # The minted fid is echoed back keyed to the submitted local_id (host adopts it).
    assert res.fids_by_local == {"local-42": fid}


# ── idempotency: re-applying the same command id is a no-op (the ledger) ─────
def test_reapplying_same_command_id_is_noop(dirs):
    root, codoc_dir = dirs
    _seed_tree(codoc_dir)
    cmd = Command(id="cmd-dup", kind="add", local_id="L1",
                  payload={"title": "Once", "description": "d"})
    append_command(codoc_dir, cmd)
    run_loop_b(root, codoc_dir, dry_run=False)

    # Re-queue the SAME id (a crash-replay / re-send). The drain sees it; the ledger
    # skips it — no second feature.
    append_command(codoc_dir, cmd)
    res2 = run_loop_b(root, codoc_dir, dry_run=False)

    assert res2.commands == 0
    s = open_store(codoc_dir)
    assert len([f for f in s.list_features() if f.title == "Once"]) == 1
    s.close()


# ── dedup: a fresh-id second add with the same (norm title, parent) is rejected ─
def test_second_add_same_title_parent_fresh_id_rejected(dirs):
    root, codoc_dir = dirs
    _seed_tree(codoc_dir)
    append_command(codoc_dir, Command(id="c-a", kind="add", local_id="L1",
                                      payload={"title": "Palette"}))
    run_loop_b(root, codoc_dir, dry_run=False)

    # Same normalized title (whitespace/case differ), same parent (None), fresh id.
    append_command(codoc_dir, Command(id="c-b", kind="add", local_id="L2",
                                      payload={"title": "  palette "}))
    res2 = run_loop_b(root, codoc_dir, dry_run=False)

    s = open_store(codoc_dir)
    assert len([f for f in s.list_features() if f.title.strip().lower() == "palette"]) == 1
    s.close()
    # The duplicate command was consumed (ledger-stamped) but minted nothing.
    assert res2.fids_by_local == {}


# ── retire: tombstones and never re-adds on a later pass ─────────────────────
def test_retire_tombstones_and_does_not_readd(dirs):
    root, codoc_dir = dirs
    f = Feature(title="Legacy", description="old")
    _seed_tree(codoc_dir, f)
    append_command(codoc_dir, Command(id="r-1", kind="retire", feature_id=f.id))

    run_loop_b(root, codoc_dir, dry_run=False)
    s = open_store(codoc_dir)
    assert s.get_feature(f.id).retired is True
    assert all(lf.id != f.id for lf in s.list_features())  # not in the live set
    s.close()

    # A subsequent pass must NOT resurrect it.
    run_loop_b(root, codoc_dir, dry_run=False)
    s2 = open_store(codoc_dir)
    assert s2.get_feature(f.id).retired is True
    s2.close()


# ── move: reparents without minting ──────────────────────────────────────────
def test_move_reparents_without_minting(dirs):
    root, codoc_dir = dirs
    parent = Feature(title="Parent", description="p")
    child = Feature(title="Child", description="c")
    _seed_tree(codoc_dir, parent, child)
    before = {f.id for f in open_store(codoc_dir).list_features()}

    append_command(codoc_dir, Command(id="m-1", kind="move", feature_id=child.id,
                                      payload={"parent_id": parent.id}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.commands == 1
    s = open_store(codoc_dir)
    assert s.get_feature(child.id).parent_id == parent.id
    assert {f.id for f in s.list_features()} == before  # no new node
    s.close()


# ── set_title / set_description: amend in place by fid ───────────────────────
def test_set_title_and_description_amend_in_place(dirs):
    root, codoc_dir = dirs
    f = Feature(title="Old title", description="Old desc.")
    _seed_tree(codoc_dir, f)
    append_command(codoc_dir, Command(id="t-1", kind="set_title", feature_id=f.id,
                                      payload={"title": "New title"}))
    append_command(codoc_dir, Command(id="d-1", kind="set_description", feature_id=f.id,
                                      payload={"description": "New desc."}))

    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.commands == 2
    s = open_store(codoc_dir)
    g = s.get_feature(f.id)
    assert g.title == "New title" and g.description == "New desc."
    s.close()


# ── drain order: commands apply BEFORE the legacy annotation edits list ──────
def test_commands_apply_before_legacy_annotation(dirs):
    """A pass carrying BOTH a `commands` entry and a legacy `edits` annotation for the
    same feature applies the command (step 0.5) first; the legacy text edit (step 2)
    then lands on the post-command state, stamped with the annotation's authorship.

    Command renames the TITLE; the raw-text edit (the annotation describes it) rewrites
    the DESCRIPTION and carries the SAME renamed title (the doc agrees with the command,
    as it will in the real flow), so the step-2 doc-diff AMEND doesn't revert the
    command. (Phase A still runs the doc-diff path; U7 retires it.)"""
    root, codoc_dir = dirs
    f = Feature(title="Feature A", description="Original prose.")
    _seed_tree(codoc_dir, f)

    # Command: rename the title (applied in step 0.5, before any annotation use).
    append_command(codoc_dir, Command(id="cmd-x", kind="set_title",
                                      feature_id=f.id, payload={"title": "Feature A renamed"}))
    # Legacy annotation: a raw-text DESCRIPTION edit authored by an agent.
    edits_channel.append_annotation(codoc_dir, edits_channel.EditAnnotation(
        feature_id=f.id, fields=["description"], actor="claude-code", mode="pen"))
    # tree.codoc carries the rename (matching the command) + the description rewrite.
    tp = tree_path(codoc_dir)
    text = tp.read_text().replace("Feature A", "Feature A renamed").replace(
        "Original prose.", "Rewritten prose.")
    tp.write_text(text)

    run_loop_b(root, codoc_dir, dry_run=False)

    s = open_store(codoc_dir)
    g = s.get_feature(f.id)
    # The command applied (title renamed) AND the post-command text edit landed.
    assert g.title == "Feature A renamed"
    assert g.description == "Rewritten prose."
    # The annotation stamped the text edit's AMEND event with the declared author —
    # the command's own AMEND was logged earlier (step 0.5), proving the order.
    fid_events = [e for e in s.recent_events(limit=20) if e.op.feature_id == f.id]
    cmd_titles = [e for e in fid_events if e.op.title == "Feature A renamed"
                  and e.op.description is None]
    desc_amends = [e for e in fid_events if e.op.description == "Rewritten prose."]
    assert cmd_titles, "command's set_title AMEND should be logged"
    assert desc_amends and desc_amends[0].actor == "claude-code"
    s.close()


# ── a command whose feature_id has vanished is skipped without crashing ──────
def test_command_with_vanished_feature_id_is_skipped(dirs):
    root, codoc_dir = dirs
    _seed_tree(codoc_dir)  # empty store — no such feature
    append_command(codoc_dir, Command(id="v-1", kind="set_title",
                                      feature_id="f-does-not-exist", payload={"title": "X"}))

    res = run_loop_b(root, codoc_dir, dry_run=False)  # must not raise

    assert res.commands == 0 and not res.error
    # The command is ledger-stamped so it isn't retried forever.
    s = open_store(codoc_dir)
    assert s.command_applied("v-1") is True
    s.close()
    # And the channel was drained.
    assert edits_channel.read_commands(codoc_dir) == []
