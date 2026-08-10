"""Phase 4 — Loop B (codoc → code).

Loop B queues code-implying tree edits for the live Claude Code session by writing
``.codoc/realize.md`` and setting status ``awaiting_impl`` — it no longer spawns a
headless ``claude -p``. These tests cover that handoff.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codoc.codoc_file.render import write_tree
from codoc.loop import inbox
from codoc.loop.loop_b import realize_path, run_loop_b
from codoc.loop.status import AWAITING_IMPL, status_path
from codoc.model.binding import Binding
from codoc.model.event import Event, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def dirs(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    return str(root), str(codoc_dir)


def _state(codoc_dir) -> str:
    return json.loads(status_path(codoc_dir).read_text())["state"]


# -----------------------------------------------------------------------
def test_accept_plan_proposal_applies_and_builds_directive(dirs):
    """Accepting a PLAN proposal (realized=False — code does NOT yet exist) builds a
    NEW FEATURE directive. A plan is an explicit build request, so it is handed off on
    accept (not held). Contrast test_accept_unbound_add_does_not_build below."""
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Theme system", realized=False,
                        description="A light/dark theme switcher.", rationale="planned"))
    s.append_event(e)
    write_tree(s, codoc_dir)
    s.close()

    inbox.append_verdict(codoc_dir, e.id, accept=True)
    res = run_loop_b(root, codoc_dir, dry_run=True)

    assert res.accepted == 1
    assert any("NEW FEATURE" in d and "Theme system" in d for d in res.directives)
    s2 = open_store(codoc_dir)
    assert any(f.title == "Theme system" for f in s2.list_features())
    assert s2.pending_events() == []
    s2.close()


def test_accept_unbound_add_does_not_build(dirs):
    """Accepting a Loop-A ADD for UNBOUND code (realized defaults None — the code
    already exists) creates the feature but mints NO directive: realizing it would ask
    the agent to re-implement code that is already there. Only an explicit plan builds."""
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Theme system",
                        description="Switches between light and dark themes.",
                        rationale="no node fits"))
    s.append_event(e)
    write_tree(s, codoc_dir)
    s.close()

    inbox.append_verdict(codoc_dir, e.id, accept=True)
    res = run_loop_b(root, codoc_dir, dry_run=True)

    assert res.accepted == 1
    assert res.directives == []          # code exists → no realize directive
    s2 = open_store(codoc_dir)
    assert any(f.title == "Theme system" for f in s2.list_features())
    s2.close()


def test_reject_proposal_drops_event(dirs):
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Doomed", description="x"))
    s.append_event(e)
    write_tree(s, codoc_dir)
    s.close()

    inbox.append_verdict(codoc_dir, e.id, accept=False)
    res = run_loop_b(root, codoc_dir, dry_run=True)

    assert res.rejected == 1 and res.directives == []
    s2 = open_store(codoc_dir)
    assert s2.pending_events() == []
    assert s2.list_features() == []
    s2.close()


def test_user_amend_builds_directive_with_bindings(dirs):
    """A description edit arrives as a `set_description` COMMAND (U3/U4) — no longer
    inferred from a tree.codoc text diff (U7). The command apply path builds the
    UPDATE FEATURE directive carrying the feature's bound code (U7 wires the codoc→
    code half onto the command path). dry_run=False because commands apply only on a
    real pass; the directive is BUILT (res.directives) regardless of hand-off."""
    from codoc.loop.edits import Command, append_command
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    f = Feature(title="Color palette", description="Holds brand colors.")
    s.upsert_feature(f)
    s.upsert_binding(Binding(feature_id=f.id, file="colors.py", symbol_path="colors.py::PALETTE", fingerprint="h"))
    write_tree(s, codoc_dir)
    s.close()

    append_command(codoc_dir, Command(
        id="cmd-amend-1", kind="set_description", feature_id=f.id,
        payload={"description": "Holds brand colors. Should also expose dark-mode variants."}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.commands == 1
    assert any("UPDATE FEATURE" in d and "colors.py::PALETTE" in d
               and "dark-mode variants" in d for d in res.directives)


def test_amend_mints_held_draft_not_realized_until_handoff(dirs):
    """Held-draft model: a doc AMEND mints a directive but it is HELD — not written to
    realize.md and not sent to the agent — until an explicit hand-off. The SYSTEM no
    longer guesses from prose whether the edit 'requests code'; the USER decides by
    handing off. A typo fix therefore never surprises the agent with code."""
    from codoc.loop.edits import Command, append_command
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    f = Feature(title="Color palette", description="Holds brand colors.")
    s.upsert_feature(f)
    s.upsert_binding(Binding(feature_id=f.id, file="colors.py",
                             symbol_path="colors.py::PALETTE", fingerprint="h"))
    write_tree(s, codoc_dir)
    s.close()

    append_command(codoc_dir, Command(
        id="cmd-held-1", kind="set_description", feature_id=f.id,
        payload={"description": "Holds brand colors and their dark-mode variants for the UI."}))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.commands == 1            # the prose edit IS applied (as a command)
    assert res.queued is False          # …but HELD — not realized
    assert not realize_path(codoc_dir).exists()
    # The directive exists in the manifest as a held draft (handed_off=False).
    from codoc.loop.edits import read_manifest
    manifest = read_manifest(codoc_dir)
    assert len(manifest) == 1 and manifest[0].handed_off is False
    # The prose persisted to the store regardless.
    s2 = open_store(codoc_dir)
    assert "dark-mode variants" in (s2.get_feature(f.id).description or "")
    s2.close()

    # Explicit hand-off (the CLI/webview gesture) → the held draft realizes.
    from codoc.loop.edits import append_handoffs
    append_handoffs(codoc_dir, [f.id])
    res2 = run_loop_b(root, codoc_dir, dry_run=False)
    assert res2.queued is True
    assert realize_path(codoc_dir).exists()


def test_code_implying_edit_queues_realize_for_session(dirs):
    """A code-implying accepted proposal is queued in .codoc/realize.md (no spawn),
    and status becomes awaiting_impl. codoc writes no code itself."""
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="New mod", realized=False,
                        description="A new.py module with a new() helper."))
    s.append_event(e)
    write_tree(s, codoc_dir)
    s.close()
    inbox.append_verdict(codoc_dir, e.id, accept=True)

    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.queued is True and not res.error
    rp = realize_path(codoc_dir)
    assert rp.exists()
    body = rp.read_text()
    assert "NEW FEATURE" in body and "New mod" in body
    assert _state(codoc_dir) == AWAITING_IMPL
    # The live session writes the code later; codoc itself creates nothing.
    assert not Path(root, "new.py").exists()


def test_dry_run_builds_directive_but_does_not_queue(dirs):
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="X", realized=False,
                        description="An x() helper."))
    s.append_event(e)
    write_tree(s, codoc_dir)
    s.close()
    inbox.append_verdict(codoc_dir, e.id, accept=True)

    res = run_loop_b(root, codoc_dir, dry_run=True)

    assert res.directives          # the directive is built…
    assert res.queued is False     # …but not written in dry-run
    assert not realize_path(codoc_dir).exists()


def test_epoch_written_files_excludes_reads(tmp_path):
    """activity.epoch_written_files counts writes only — reads are not writes."""
    from codoc.loop.activity import ACTIVITY_FILENAME, epoch_written_files
    cd = tmp_path / ".codoc"
    cd.mkdir()
    (cd / ACTIVITY_FILENAME).write_text(json.dumps({
        "version": 1,
        "epoch": {"id": "ep-x", "origin": "loop_b", "open": False},
        "touched": {
            "wrote.py": {"mode": "write"},
            "only_read.py": {"mode": "read"},
        },
        "recent": [],
    }))
    assert epoch_written_files(cd) == ["wrote.py"]


def test_accepted_auto_retire_is_detach_only_no_directive(dirs):
    """Accepting a Loop-A-raised RETIRE untracks the feature (retired + detached)
    but must NOT queue a code-deletion directive — a false auto-retire can no
    longer destroy live code on accept."""
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    f = Feature(title="Doc retrieval index", description="Builds a search index.")
    s.upsert_feature(f)
    s.upsert_binding(Binding(feature_id=f.id, file="retrieval.py",
                             symbol_path="retrieval.py::Retriever", fingerprint="h"))
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=f.id, rationale="lost binding"))
    s.append_event(e)
    write_tree(s, codoc_dir)
    s.close()

    inbox.append_verdict(codoc_dir, e.id, accept=True)
    res = run_loop_b(root, codoc_dir, dry_run=True)

    assert res.accepted == 1
    assert res.directives == []                       # no "remove this code" directive
    s2 = open_store(codoc_dir)
    assert s2.get_feature(f.id).retired is True        # untracked on accept
    assert s2.bindings_for_feature(f.id) == []         # detached, not orphaned under a hidden feature
    assert s2.binding_at("retrieval.py", "retrieval.py::Retriever") is None
    s2.close()


def test_command_retire_is_detach_only_soft_retire_no_directive(dirs):
    """A webview `retire` COMMAND (U3/U4 — the human's delete gesture) is a SOFT,
    DETACH-ONLY retire: it marks the feature retired AND detaches its bindings, but
    queues NO code-deletion directive (FIX A). None of the five webview command kinds
    set delete_code, so deleting a doc node removes the FEATURE, not the code — the
    old reconcile_doc_presence behavior. Code removal is reserved for an explicit
    delete_code retire (the agent `~` path, exercised by the inbox test below)."""
    from codoc.loop.edits import Command, append_command
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    f = Feature(title="Legacy export", description="Old CSV export path.")
    s.upsert_feature(f)
    s.upsert_binding(Binding(feature_id=f.id, file="export.py",
                             symbol_path="export.py::to_csv", fingerprint="h"))
    write_tree(s, codoc_dir)
    s.close()

    append_command(codoc_dir, Command(id="cmd-retire-1", kind="retire", feature_id=f.id))
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.commands == 1
    # ZERO directives for the retired feature — never a "remove this code" request.
    assert res.directives == []
    assert not any("RETIRE FEATURE" in d for d in res.directives)
    s2 = open_store(codoc_dir)
    assert s2.get_feature(f.id).retired is True          # soft-retired (marked, not deleted)
    assert s2.bindings_for_feature(f.id) == []           # detached, not orphaned under a hidden feature
    assert s2.binding_at("export.py", "export.py::to_csv") is None
    s2.close()


def test_accepted_delete_code_retire_queues_removal_directive(dirs):
    """An explicit delete-code retire (op.delete_code, e.g. an agent via MCP) is the
    parity for a human `~`: accepting keeps its bindings and queues a code-removal
    directive — unlike the detach-only default."""
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    f = Feature(title="Legacy thing", description="Old path.")
    s.upsert_feature(f)
    s.upsert_binding(Binding(feature_id=f.id, file="legacy.py",
                             symbol_path="legacy.py::run", fingerprint="h"))
    e = Event(source="loop_a_agent", applied=False,
              op=NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=f.id, delete_code=True))
    s.append_event(e)
    write_tree(s, codoc_dir)
    s.close()

    inbox.append_verdict(codoc_dir, e.id, accept=True)
    res = run_loop_b(root, codoc_dir, dry_run=True)

    assert res.accepted == 1
    assert any("RETIRE FEATURE" in d and "legacy.py::run" in d for d in res.directives)
    s2 = open_store(codoc_dir)
    # delete_code keeps bindings (the agent removes the code; reconcile detaches then)
    assert s2.get_feature(f.id).retired is True
    assert s2.binding_at("legacy.py", "legacy.py::run") is not None
    s2.close()


def test_accept_all_over_a_mixed_batch_leaves_no_proposal_behind(dirs):
    """"Accept all" — the toolbar posting every pending event id in one write — must
    resolve EVERY kind in the batch, and a retired node must then be gone from both
    surfaces the IDE renders (the doc projection and tree.codoc).

    Pinned after a report of a node still showing after Accept all: the accepted retire
    has to leave the projection, not merely be flagged, or the reader is looking at a
    feature the store no longer has.
    """
    from codoc.codoc_file.doc_render import build_doc_from_store
    from codoc.codoc_file.render import render_tree

    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    parent = Feature(title="Session request lifecycle", description="The session.")
    s.upsert_feature(parent)
    doomed = Feature(title="Session QUERY helper", description="Adds Session.query().",
                     parent_id=parent.id)
    s.upsert_feature(doomed)
    s.upsert_binding(Binding(feature_id=doomed.id, file="sessions.py",
                             symbol_path="sessions.py::Session.query", fingerprint="h"))
    drifted = Feature(title="Redirect handling mixin", description="Old text.",
                      parent_id=parent.id)
    s.upsert_feature(drifted)
    mover = Feature(title="Transport adapter", description="Carries a request.")
    s.upsert_feature(mover)

    events = [
        Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=doomed.id, rationale="gone")),
        Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.AMEND, feature_id=drifted.id,
                        description="New text describing the mixin.", rationale="drift")),
        Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, parent_id=parent.id,
                        title="Session setting merge helpers",
                        description="Combines request- and session-level options.")),
        Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id=mover.id, parent_id=parent.id)),
    ]
    for e in events:
        s.append_event(e)
    write_tree(s, codoc_dir)
    s.close()

    for e in events:                       # exactly what the toolbar's Accept all posts
        inbox.append_verdict(codoc_dir, e.id, accept=True)
    res = run_loop_b(root, codoc_dir, dry_run=True)

    assert res.accepted == 4
    assert res.rejected == 0
    s2 = open_store(codoc_dir)
    assert s2.pending_events() == []                    # nothing left pending anywhere
    assert s2.get_feature(doomed.id).retired is True
    assert s2.bindings_for_feature(doomed.id) == []     # detached, not orphaned
    assert s2.get_feature(drifted.id).description == "New text describing the mixin."
    assert s2.get_feature(mover.id).parent_id == parent.id

    titles = {f.title for f in s2.list_features()}      # live only
    assert "Session QUERY helper" not in titles
    assert "Session setting merge helpers" in titles

    # …and the retired node is absent from BOTH rendered surfaces, not just the store.
    doc_titles = [
        b["content"][0]["text"] for b in build_doc_from_store(s2)["content"]
        if b["type"] == "featureHeading" and b.get("content")
    ]
    assert "Session QUERY helper" not in doc_titles
    assert "Session QUERY helper" not in render_tree(s2)
    s2.close()
