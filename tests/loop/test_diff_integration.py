"""Phase 2 — compute_changeset against the real cocoindex/LanceDB index.

Skips if the indexing substrate is unavailable (e.g. embedding model can't be
downloaded offline), so it never blocks the unit suite.
"""
from __future__ import annotations

import pytest

from codoc.loop.diff import compute_changeset


def test_compute_changeset_added_modified_removed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def foo():\n    return 1\n")
    codoc_dir = str(tmp_path / ".codoc")

    try:
        cs1 = compute_changeset(str(repo), codoc_dir)
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"indexing substrate unavailable: {e}")

    assert any("foo" in c.symbol_path for c in cs1.added)

    # modify the body → modified
    (repo / "a.py").write_text("def foo():\n    return 2\n")
    cs2 = compute_changeset(str(repo), codoc_dir)
    assert any("foo" in c.symbol_path for c in cs2.modified)
    assert not cs2.added

    # add a new symbol → added
    (repo / "b.py").write_text("def bar():\n    return 3\n")
    cs3 = compute_changeset(str(repo), codoc_dir)
    assert any("bar" in c.symbol_path for c in cs3.added)

    # delete a file → removed
    (repo / "b.py").unlink()
    cs4 = compute_changeset(str(repo), codoc_dir)
    assert any("bar" in c.symbol_path for c in cs4.removed)
