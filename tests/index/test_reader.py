"""Scoped/projected reads over the LanceDB chunk index.

``read_all_chunks`` must push the file filter down to LanceDB and be able to
drop the heavy ``source`` column; ``compute_changeset`` reads source only for
the files that changed.
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


def test_compute_changeset_reads_source_only_for_the_files_that_changed(tmp_path, monkeypatch):
    """Both snapshots leave the source column out; source is read for the
    files whose chunks changed, and for every row with ``all_sources``."""
    import codoc.index.changes as diff_mod
    from codoc.index.reader import ChunkRow

    def r(file, sym, fp):
        return ChunkRow(id=0, file=file, symbol=f"{file}::{sym}", language="python", source=f"def {sym}(): {fp}",
                        fingerprint=fp, types_hash="t", start_byte=0, end_byte=1)
    before, after = [r("a.py", "foo", "1"), r("b.py", "baz", "1")], [r("a.py", "foo", "2"), r("b.py", "baz", "1")]
    calls: list = []

    def fake_read(codoc_dir, *, files=None, with_source=True):
        calls.append((files, with_source))
        rows = after if len(calls) > 1 else before
        return [ChunkRow(**{**vars(x), "source": x.source if with_source else ""})
                for x in rows if files is None or x.file in files]

    monkeypatch.setattr(diff_mod, "read_all_chunks", fake_read)
    monkeypatch.setattr(diff_mod, "update_index", lambda *a, **k: None)

    cs = diff_mod.compute_changeset("root", str(tmp_path))
    assert calls == [(None, False), (None, False), ({"a.py"}, True)]
    assert [(c.symbol, c.source) for c in cs.modified] == [("a.py::foo", "def foo(): 2")]
    assert {x.symbol: x.source for x in cs.rows} == {"a.py::foo": "def foo(): 2", "b.py::baz": ""}

    calls.clear()
    cs = diff_mod.compute_changeset("root", str(tmp_path), all_sources=True)
    assert calls == [(None, False), (None, False), (None, True)] and all(x.source for x in cs.rows)
