"""Phase 4 — Loop B (codoc → code). No real `claude` spawn in CI."""
from __future__ import annotations

from pathlib import Path

import pytest

from codoc.codoc_file.render import tree_path, write_tree
from codoc.loop.loop_a import LoopAResult
from codoc.loop.loop_b import run_loop_b
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


def _flip(path: Path, event_id: str, new: str) -> None:
    out = []
    for line in path.read_text().splitlines():
        if event_id in line and line.lstrip().startswith("?"):
            line = line.replace("?", new, 1)
        out.append(line)
    path.write_text("\n".join(out) + "\n")


def _edit_file(path: Path, old: str, new: str) -> None:
    path.write_text(path.read_text().replace(old, new))


# -----------------------------------------------------------------------
def test_accept_proposal_applies_and_builds_directive(dirs):
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Theme system",
                        description="Switches light/dark theme.", rationale="no node fits"))
    s.append_event(e)
    write_tree(s, codoc_dir)
    s.close()

    _flip(tree_path(codoc_dir), e.id, "+")
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

    _flip(tree_path(codoc_dir), e.id, "-")
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

    _edit_file(tree_path(codoc_dir), "Holds brand colors.", "Holds brand colors and dark-mode variants.")
    res = run_loop_b(root, codoc_dir, dry_run=True)

    assert res.user_edits >= 1
    assert any("UPDATE FEATURE" in d and "colors.py::PALETTE" in d
               and "dark-mode variants" in d for d in res.directives)


def test_spawn_and_refine_loop_closure(dirs):
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    e = Event(source="loop_a", applied=False,
              op=NodeOp(kind=NodeOpKind.ADD_NODE, title="New mod", description="adds new.py"))
    s.append_event(e)
    write_tree(s, codoc_dir)
    s.close()
    _flip(tree_path(codoc_dir), e.id, "+")

    calls = {}

    def fake_spawn(prompt, root_dir, **kw):
        calls["prompt"] = prompt
        Path(root_dir, "new.py").write_text("def new():\n    return 1\n")  # agent writes a file
        return 0, "done"

    sentinel = LoopAResult(auto={"refresh": 0})

    def fake_refine(root_dir, codoc_dir, *, file_scope=None, source="loop_b", config=None):
        calls["refine_scope"] = file_scope
        calls["refine_source"] = source
        return sentinel

    res = run_loop_b(root, codoc_dir, dry_run=False, spawn=fake_spawn, refine=fake_refine)

    assert res.spawned is True and not res.error
    assert "NEW FEATURE" in calls["prompt"]
    assert "new.py" in res.files_written
    assert calls["refine_scope"] == {"new.py"} and calls["refine_source"] == "loop_b"
    assert res.refinement is sentinel


def test_spawn_failure_is_captured(dirs):
    root, codoc_dir = dirs
    s = open_store(codoc_dir)
    f = Feature(title="X", description="d")
    s.upsert_feature(f)
    write_tree(s, codoc_dir)
    _edit_file(tree_path(codoc_dir), "    d", "    a brand new much longer intent description here")
    s.close()

    def boom(prompt, root_dir, **kw):
        raise FileNotFoundError("claude not installed")

    res = run_loop_b(root, codoc_dir, dry_run=False, spawn=boom)
    assert "claude not installed" in res.error
    assert res.spawned is False
