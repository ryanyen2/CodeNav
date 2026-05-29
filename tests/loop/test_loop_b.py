"""Phase 4 — Loop B (codoc → code). No real `claude` spawn in CI."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codoc.codoc_file.render import tree_path, write_tree
from codoc.loop import inbox
from codoc.loop.activity import ACTIVITY_FILENAME
from codoc.loop.loop_a import LoopAResult
from codoc.loop.loop_b import _spawn_claude, run_loop_b
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


def test_descriptive_amend_on_bound_feature_does_not_spawn(dirs):
    """The core WS1 fix: documenting existing code (descriptive prose) never
    builds a directive / spawns the agent — only imperative intent does."""
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
    spawned = {"called": False}

    def boom(prompt, root_dir, **kw):
        spawned["called"] = True
        return 0, ""

    res = run_loop_b(root, codoc_dir, dry_run=False, spawn=boom)

    assert res.user_edits >= 1          # the prose edit IS applied
    assert res.directives == []         # …but it does not imply code
    assert res.spawned is False
    assert spawned["called"] is False
    # And the prose persisted to the store.
    s2 = open_store(codoc_dir)
    assert "dark-mode variants" in (s2.get_feature(f.id).description or "")
    s2.close()


def test_spawn_and_refine_loop_closure(dirs):
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="New mod",
                        description="Add a new.py module with a new() helper."))
    s.append_event(e)
    write_tree(s, codoc_dir)
    s.close()
    inbox.append_verdict(codoc_dir, e.id, accept=True)

    calls = {}

    def fake_spawn(prompt, root_dir, **kw):
        calls["prompt"] = prompt
        Path(root_dir, "new.py").write_text("def new():\n    return 1\n")  # agent writes a file
        return 0, "done"

    sentinel = LoopAResult(auto={"refresh": 0})

    def fake_refine(root_dir, codoc_dir, *, file_scope=None, source="loop_b", config=None, **kw):
        calls["refine_scope"] = file_scope
        calls["refine_source"] = source
        calls["adopt"] = kw.get("adopt_placeholders")
        return sentinel

    res = run_loop_b(root, codoc_dir, dry_run=False, spawn=fake_spawn, refine=fake_refine)

    assert res.spawned is True and not res.error
    assert "NEW FEATURE" in calls["prompt"]
    assert "new.py" in res.files_written
    assert calls["refine_scope"] == {"new.py"} and calls["refine_source"] == "loop_b"
    assert calls["adopt"] is True
    assert res.refinement is sentinel


def test_precise_reflect_uses_activity_json(dirs):
    """When activity.json records touched files, Loop B uses them (not mtime walk)."""
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    f = Feature(title="Widget", description="A UI widget.", id="f-widget")
    s.upsert_feature(f)
    write_tree(s, codoc_dir)
    s.close()

    # Pre-write inbox verdict so Loop B spawns.
    from codoc.codoc_file.render import write_tree as wt
    from codoc.codoc_file.diff import diff_codoc
    from codoc.codoc_file.parse import parse_tree_file
    from codoc.model.event import PLAN_SOURCE
    from codoc.loop.apply import apply_op
    s2 = open_store(codoc_dir)
    op = NodeOp(kind=NodeOpKind.ADD_NODE, title="New", description="Implement d.")
    e = apply_op(op, s2, source=PLAN_SOURCE, applied=False)
    wt(s2, codoc_dir)
    s2.close()
    inbox.append_verdict(codoc_dir, e.id, accept=True)

    # Write activity.json with a specific touched file.
    activity_data = {
        "version": 1,
        "epoch": {"id": "ep-lb", "origin": "loop_b", "open": False,
                  "started_at": None, "ended_at": None},
        "touched": {"from_activity.py": {"feature_ids": [], "last": None, "mode": "write"}},
        "recent": [],
    }
    (Path(codoc_dir) / ACTIVITY_FILENAME).write_text(json.dumps(activity_data))

    calls: dict = {}

    def fake_spawn(prompt, root_dir, **kw):
        # Agent writes a DIFFERENT file than what's in activity.json.
        Path(root_dir, "from_mtime.py").write_text("x = 1\n")
        return 0, "ok"

    def fake_refine(root_dir, codoc_dir, *, file_scope=None, source="loop_b", config=None, **kw):
        calls["file_scope"] = file_scope
        return LoopAResult()

    run_loop_b(root, codoc_dir, dry_run=False, spawn=fake_spawn, refine=fake_refine)

    # Activity.json file should be used, not the mtime-discovered file.
    assert "from_activity.py" in (calls.get("file_scope") or set()), \
        f"Expected activity.json path in file_scope, got {calls.get('file_scope')}"


def test_epoch_written_files_excludes_reads(tmp_path):
    """'agent wrote N files' must count writes only — reads are not writes."""
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


def test_spawn_claude_sets_loop_b_origin_env(tmp_path):
    """_spawn_claude must pass CODOC_EPOCH_ORIGIN=loop_b in the subprocess env."""
    captured_env: dict = {}

    def fake_run(cmd, **kw):
        captured_env.update(kw.get("env", {}))
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        return m

    with patch("codoc.loop.loop_b.subprocess.run", side_effect=fake_run):
        _spawn_claude("echo hi", str(tmp_path))

    assert captured_env.get("CODOC_EPOCH_ORIGIN") == "loop_b"


def test_spawn_failure_is_captured(dirs):
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    f = Feature(title="X", description="d")
    s.upsert_feature(f)
    write_tree(s, codoc_dir)
    _edit_file(tree_path(codoc_dir), "    d", "    Implement a brand new helper for this.")
    s.close()

    def boom(prompt, root_dir, **kw):
        raise FileNotFoundError("claude not installed")

    res = run_loop_b(root, codoc_dir, dry_run=False, spawn=boom)
    assert "claude not installed" in res.error
    assert res.spawned is False
