"""Loop B coalesces directives per feature (the field "weird count" bug).

A user iterating on ONE feature's description — rewording, undo/redo, removing an
added sentence — used to APPEND a new realize directive on every settle, stacking N
directives for a single feature (observed in the field: 5 directives queued for one
feature). The queue must instead coalesce: a fresh user AMEND to a feature SUPERSEDES
its earlier un-synced directive (latest wins), and reverting to a descriptive (non
code-implying) text WITHDRAWS the queued change entirely. Steers and other features'
directives are untouched.
"""
from __future__ import annotations

from codoc.codoc_file.render import tree_path, write_tree
from codoc.loop.edits import read_manifest
from codoc.loop.loop_b import realize_path, run_loop_b
from codoc.model.binding import Binding
from codoc.model.feature import Feature
from codoc.store.db import open_store

import pytest


@pytest.fixture
def dirs(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    codoc_dir = tmp_path / ".codoc"; codoc_dir.mkdir()
    return str(root), str(codoc_dir)


def _seed(codoc_dir, title, description, file):
    s = open_store(codoc_dir)
    f = Feature(title=title, description=description)
    s.upsert_feature(f)
    s.upsert_binding(Binding(feature_id=f.id, file=file,
                             symbol_path=f"{file}::SYM", fingerprint="h"))
    write_tree(s, codoc_dir)
    s.close()
    return f


def _edit(codoc_dir, old, new):
    p = tree_path(codoc_dir)
    p.write_text(p.read_text().replace(old, new))


def test_iterating_one_feature_coalesces_to_one_directive(dirs):
    root, codoc_dir = dirs
    _seed(codoc_dir, "Palette", "Holds brand colors.", "colors.py")
    _edit(codoc_dir, "Holds brand colors.", "Should also support dark-mode palettes.")
    run_loop_b(root, codoc_dir, dry_run=False)
    assert len(read_manifest(codoc_dir)) == 1

    # Iterate the SAME feature — must REPLACE, not stack.
    _edit(codoc_dir, "Should also support dark-mode palettes.",
          "Should also support dark-mode and high-contrast palettes.")
    run_loop_b(root, codoc_dir, dry_run=False)
    m = read_manifest(codoc_dir)
    assert len(m) == 1, f"iterating stacked {len(m)} directives instead of coalescing"
    assert "high-contrast" in m[0].text  # the latest edit won


def test_reverting_to_descriptive_withdraws_the_queued_directive(dirs):
    root, codoc_dir = dirs
    _seed(codoc_dir, "Palette", "Holds brand colors.", "colors.py")
    _edit(codoc_dir, "Holds brand colors.", "Should also support dark-mode palettes.")
    run_loop_b(root, codoc_dir, dry_run=False)
    assert len(read_manifest(codoc_dir)) == 1

    # Remove the imperative sentence (revert to descriptive) → withdraw the queued change.
    _edit(codoc_dir, "Should also support dark-mode palettes.", "Holds brand and dark-mode colors.")
    run_loop_b(root, codoc_dir, dry_run=False)
    assert read_manifest(codoc_dir) == []
    assert not realize_path(codoc_dir).exists()


def test_editing_one_feature_keeps_anothers_queued_directive(dirs):
    root, codoc_dir = dirs
    fa = _seed(codoc_dir, "A", "Desc A.", "a.py")
    # add a second feature into the same store
    s = open_store(codoc_dir)
    fb = Feature(title="B", description="Desc B.")
    s.upsert_feature(fb)
    s.upsert_binding(Binding(feature_id=fb.id, file="b.py", symbol_path="b.py::SYM", fingerprint="h"))
    write_tree(s, codoc_dir); s.close()

    _edit(codoc_dir, "Desc A.", "Should rewrite A entirely.")
    run_loop_b(root, codoc_dir, dry_run=False)
    _edit(codoc_dir, "Desc B.", "Should rewrite B entirely.")
    run_loop_b(root, codoc_dir, dry_run=False)

    m = read_manifest(codoc_dir)
    assert {d.feature_id for d in m} == {fa.id, fb.id}  # editing B did NOT drop A's directive
    assert len(m) == 2
