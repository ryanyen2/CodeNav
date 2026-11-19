"""Scoped/projected reads over the LanceDB chunk index.

``read_all_chunks`` must push the file filter down to LanceDB and be able to
drop the heavy ``source`` column; ``compute_changeset`` must do at most one
unscoped read per pass.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from codoc.index.reader import read_all_chunks
from codoc.index.schema import LANCE_TABLE_NAME

lancedb = pytest.importorskip("lancedb")


def _make_index(codoc_dir: Path) -> None:
    db = lancedb.connect(str(codoc_dir / "lancedb"))
    rows = [
        {"id": i, "file": f, "symbol": f"{f}::{sym}", "language": "python",
         "source": f"def {sym}(): ...", "fingerprint": f"tok{i}", "types_hash": f"ty{i}",
         "start_byte": 0, "end_byte": 10}
        for i, (f, sym) in enumerate([("a.py", "foo"), ("a.py", "bar"), ("b.py", "baz")])
    ]
    db.create_table(LANCE_TABLE_NAME, rows)


def test_files_filter_pushed_down(tmp_path):
    _make_index(tmp_path)
    rows = read_all_chunks(tmp_path, files={"a.py"})
    assert sorted(r.symbol for r in rows) == ["a.py::bar", "a.py::foo"]
    assert read_all_chunks(tmp_path, files=set()) == []
    assert len(read_all_chunks(tmp_path)) == 3


def test_source_column_droppable(tmp_path):
    _make_index(tmp_path)
    light = read_all_chunks(tmp_path, with_source=False)
    assert all(r.source == "" for r in light)
    assert all(r.fingerprint for r in light)  # identity columns intact
    assert all(r.source for r in read_all_chunks(tmp_path))


def test_missing_index_returns_empty(tmp_path):
    assert read_all_chunks(tmp_path) == []


def test_compute_changeset_read_contract(tmp_path, monkeypatch):
    """Scoped pass: scoped reads carry files=…, and at most ONE unscoped read
    (the light symbol table)."""
    import codoc.index.changes as diff_mod

    calls: list[dict] = []

    def fake_read(codoc_dir, *, files=None, with_source=True):
        calls.append({"files": files, "src": with_source})
        return []

    monkeypatch.setattr(diff_mod, "read_all_chunks", fake_read)
    monkeypatch.setattr(diff_mod, "update_index", lambda *a, **k: None)

    diff_mod.compute_changeset("root", str(tmp_path), file_scope={"a.py"})
    unscoped = [c for c in calls if c["files"] is None]
    assert len(unscoped) == 1 and unscoped[0]["src"] is False

    calls.clear()
    diff_mod.compute_changeset("root", str(tmp_path), file_scope=None)
    assert len(calls) == 2
