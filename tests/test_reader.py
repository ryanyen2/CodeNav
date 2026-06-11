"""Scoped/projected reads over the LanceDB chunk index (Phase-4 incrementality).

``read_all_chunks`` must push the file filter down to LanceDB and be able to
drop the two heavy columns (embedding, source); ``compute_changeset`` must do
at most one unscoped read per pass — and never read embeddings.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from codoc.pipelines.indexing.reader import read_all_chunks
from codoc.pipelines.indexing.schema import LANCE_TABLE_NAME

lancedb = pytest.importorskip("lancedb")


def _make_index(codoc_dir: Path) -> None:
    db = lancedb.connect(str(codoc_dir / "lancedb"))
    rows = [
        {"id": i, "file": f, "symbol_path": f"{f}::{sym}", "language": "python",
         "source": f"def {sym}(): ...", "tokens_hash": f"tok{i}", "types_hash": f"ty{i}",
         "start_byte": 0, "end_byte": 10, "embedding": [0.1] * 4}
        for i, (f, sym) in enumerate([("a.py", "foo"), ("a.py", "bar"), ("b.py", "baz")])
    ]
    db.create_table(LANCE_TABLE_NAME, rows)


def test_files_filter_pushed_down(tmp_path):
    _make_index(tmp_path)
    rows = read_all_chunks(tmp_path, files={"a.py"})
    assert sorted(r.symbol_path for r in rows) == ["a.py::bar", "a.py::foo"]
    assert read_all_chunks(tmp_path, files=set()) == []
    assert len(read_all_chunks(tmp_path)) == 3


def test_heavy_columns_droppable(tmp_path):
    _make_index(tmp_path)
    light = read_all_chunks(tmp_path, with_embeddings=False, with_source=False)
    assert all(r.embedding is None and r.source == "" for r in light)
    assert all(r.tokens_hash for r in light)  # identity columns intact
    full = read_all_chunks(tmp_path)
    assert all(r.embedding is not None and r.source for r in full)


def test_missing_index_returns_empty(tmp_path):
    assert read_all_chunks(tmp_path) == []


def test_compute_changeset_read_contract(tmp_path, monkeypatch):
    """Scoped pass: scoped reads carry files=…, at most ONE unscoped read (the
    light symbol table), and embeddings are never requested."""
    import codoc.loop.diff as diff_mod

    calls: list[dict] = []

    def fake_read(codoc_dir, *, files=None, with_embeddings=True, with_source=True):
        calls.append({"files": files, "emb": with_embeddings, "src": with_source})
        return []

    monkeypatch.setattr(diff_mod, "read_all_chunks", fake_read)
    monkeypatch.setattr(diff_mod, "update_index", lambda *a, **k: None)

    diff_mod.compute_changeset("root", str(tmp_path), file_scope={"a.py"})
    assert all(c["emb"] is False for c in calls)
    unscoped = [c for c in calls if c["files"] is None]
    assert len(unscoped) == 1 and unscoped[0]["src"] is False

    calls.clear()
    diff_mod.compute_changeset("root", str(tmp_path), file_scope=None)
    assert len(calls) == 2 and all(c["emb"] is False for c in calls)
