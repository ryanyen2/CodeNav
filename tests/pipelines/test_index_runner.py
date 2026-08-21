"""What the runner owes a caller that indexes more than one workspace.

Where an index lives is decided once per process, when cocoindex's single
environment enters its lifespan, so a second workspace used to write its rows into
the first one's index and leave its own empty — with no error to read. These run the
real indexer, because the claim is about that binding and nothing else can show it.
"""
from __future__ import annotations

import pathlib

from codoc.pipelines.indexing.reader import read_all_chunks
from codoc.pipelines.indexing.runner import update_index

MODULE = '''"""Sum a column."""


def total(rows):
    return sum(rows)
'''


def _workspace(root: pathlib.Path, body: str) -> pathlib.Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "sums.py").write_text(body, encoding="utf-8")
    return root


def _index(root: pathlib.Path) -> dict[str, str]:
    update_index(root, root / ".codoc")
    return {row.symbol_path: row.tokens_hash for row in read_all_chunks(root / ".codoc")}


def test_each_workspace_indexed_in_one_process_gets_its_own_index(tmp_path):
    """Two repos, one process. Each index holds that repo's code and only that."""
    first = _workspace(tmp_path / "first", MODULE)
    second = _workspace(tmp_path / "second", MODULE.replace("total", "count"))

    assert "sums.py::total" in _index(first)
    second_rows = _index(second)
    assert "sums.py::count" in second_rows
    assert "sums.py::total" not in second_rows


def test_returning_to_a_workspace_finds_it_as_it_was_left(tmp_path):
    """Switching away closes the environment; the workspace's own state is what says
    what it holds, so coming back must not depend on having stayed."""
    first = _workspace(tmp_path / "first", MODULE)
    second = _workspace(tmp_path / "second", MODULE.replace("total", "count"))

    before = _index(first)
    _index(second)
    assert _index(first) == before


def test_a_workspace_reindexed_after_its_index_was_wiped_comes_back(tmp_path):
    """The wipe path drops the connection to a directory it just deleted, so the next
    pass has to open a new one rather than write through the old."""
    import shutil

    root = _workspace(tmp_path / "only", MODULE)
    before = _index(root)
    shutil.rmtree(root / ".codoc")
    assert _index(root) == before
