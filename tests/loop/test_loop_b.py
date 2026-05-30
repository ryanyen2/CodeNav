"""Phase 4 — Loop B (codoc → code).

Loop B queues code-implying tree edits for the live Claude Code session by writing
``.codoc/realize.md`` and setting status ``awaiting_impl`` — it no longer spawns a
headless ``claude -p``. These tests cover that handoff.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codoc.codoc_file.render import tree_path, write_tree
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


def _edit_file(path: Path, old: str, new: str) -> None:
    path.write_text(path.read_text().replace(old, new))


def _state(codoc_dir) -> str:
    return json.loads(status_path(codoc_dir).read_text())["state"]


# -----------------------------------------------------------------------
def test_accept_proposal_applies_and_builds_directive(dirs):
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Theme system",
                        description="Add a light/dark theme switcher.", rationale="no node fits"))
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
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    f = Feature(title="Color palette", description="Holds brand colors.")
    s.upsert_feature(f)
    s.upsert_binding(Binding(feature_id=f.id, file="colors.py", symbol_path="colors.py::PALETTE", fingerprint="h"))
    write_tree(s, codoc_dir)
    s.close()

    _edit_file(tree_path(codoc_dir), "Holds brand colors.",
               "Holds brand colors. Should also expose dark-mode variants.")
    res = run_loop_b(root, codoc_dir, dry_run=True)

    assert res.user_edits >= 1
    assert any("UPDATE FEATURE" in d and "colors.py::PALETTE" in d
               and "dark-mode variants" in d for d in res.directives)


def test_descriptive_amend_does_not_queue(dirs):
    """Documenting existing code (descriptive prose) never queues a directive —
    only imperative intent does."""
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    f = Feature(title="Color palette", description="Holds brand colors.")
    s.upsert_feature(f)
    s.upsert_binding(Binding(feature_id=f.id, file="colors.py",
                             symbol_path="colors.py::PALETTE", fingerprint="h"))
    write_tree(s, codoc_dir)
    s.close()

    # A purely descriptive elaboration — no "should/must/add/implement…".
    _edit_file(tree_path(codoc_dir), "Holds brand colors.",
               "Holds brand colors and their dark-mode variants for the UI.")
    res = run_loop_b(root, codoc_dir, dry_run=False)

    assert res.user_edits >= 1          # the prose edit IS applied
    assert res.directives == []         # …but it does not imply code
    assert res.queued is False
    assert not realize_path(codoc_dir).exists()
    # And the prose persisted to the store.
    s2 = open_store(codoc_dir)
    assert "dark-mode variants" in (s2.get_feature(f.id).description or "")
    s2.close()


def test_code_implying_edit_queues_realize_for_session(dirs):
    """A code-implying accepted proposal is queued in .codoc/realize.md (no spawn),
    and status becomes awaiting_impl. codoc writes no code itself."""
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="New mod",
                        description="Add a new.py module with a new() helper."))
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
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="X",
                        description="Add an x() helper."))
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
