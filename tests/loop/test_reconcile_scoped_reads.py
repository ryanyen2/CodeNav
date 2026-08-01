"""Regression tests for reconcile_drift's scoped-read restructure (2026-08-01).

The P0 from the review: a file whose ONLY divergence is a REMOVED symbol never
entered the sourced-fetch candidate set, yet its file lands in graph_scope — so
update_graph re-extracted the file's surviving rows from source='' and silently
wiped every call/import edge out of that file. Also covers the empty-index
invariant (a torn/absent index must not mass-detach real bindings).
"""
from __future__ import annotations

import pytest

from codoc.graph.query import build_graph
from codoc.loop.loop_a import reconcile_drift
from codoc.model.binding import Binding
from codoc.model.feature import Feature
from codoc.pipelines.indexing.reader import ChunkRow, invalidate_cache
from codoc.pipelines.indexing.schema import LANCE_TABLE_NAME
from codoc.store.db import open_store

lancedb = pytest.importorskip("lancedb")


FOO_SRC = "def foo():\n    return helper()\n"
HELPER_SRC = "def helper():\n    return 1\n"


def _make_index(codoc_dir, rows):
    db = lancedb.connect(str(codoc_dir / "lancedb"))
    db.create_table(LANCE_TABLE_NAME, rows)
    invalidate_cache(codoc_dir)


def _chunk_row(file, sym, source, tok):
    return {"id": hash((file, sym)) % 10**9, "file": file, "symbol_path": sym,
            "language": "python", "source": source, "tokens_hash": tok,
            "types_hash": f"ty-{sym}", "start_byte": 0, "end_byte": len(source)}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    # The index-refresh is exercised elsewhere; here the pre-built table IS the
    # current index state.
    import codoc.pipelines.indexing.runner as runner
    monkeypatch.setattr(runner, "update_index", lambda *a, **k: None)
    return codoc_dir


def test_removed_symbol_keeps_surviving_call_edges(workspace):
    """Deleting bar (leaving foo untouched) must NOT wipe foo's call edges."""
    codoc_dir = workspace
    # Current index: foo (x.py) and helper (y.py). bar already deleted.
    _make_index(codoc_dir, [
        _chunk_row("x.py", "x.py::foo", FOO_SRC, "tok-foo"),
        _chunk_row("y.py", "y.py::helper", HELPER_SRC, "tok-helper"),
    ])
    with open_store(codoc_dir) as store:
        fa = Feature(title="Foo stuff")
        fb = Feature(title="Helper stuff")
        store.upsert_feature(fa)
        store.upsert_feature(fb)
        # Fingerprints match the index → nothing is "modified"; bar's binding is
        # the file's only divergence (a removal).
        store.upsert_binding(Binding(feature_id=fa.id, file="x.py",
                                     symbol_path="x.py::foo", fingerprint="tok-foo"))
        store.upsert_binding(Binding(feature_id=fa.id, file="x.py",
                                     symbol_path="x.py::bar", fingerprint="tok-bar"))
        store.upsert_binding(Binding(feature_id=fb.id, file="y.py",
                                     symbol_path="y.py::helper", fingerprint="tok-helper"))
        # Graph as it stood before the deletion.
        build_graph(store, [
            ChunkRow(id=1, file="x.py", symbol_path="x.py::foo", language="python",
                     source=FOO_SRC, tokens_hash="tok-foo", types_hash="t",
                     start_byte=0, end_byte=len(FOO_SRC)),
            ChunkRow(id=2, file="y.py", symbol_path="y.py::helper", language="python",
                     source=HELPER_SRC, tokens_hash="tok-helper", types_hash="t",
                     start_byte=0, end_byte=len(HELPER_SRC)),
        ])
        assert any(e["dst_symbol"] == "y.py::helper"
                   for e in store.edges_out("x.py::foo")), "precondition: call edge exists"

    res = reconcile_drift(str(codoc_dir.parent), str(codoc_dir))

    with open_store(codoc_dir) as store:
        # bar's binding was detached (the removal reconciled)…
        assert store.binding_at("x.py", "x.py::bar") is None
        # …and foo's call edge SURVIVED the re-extraction of x.py.
        assert any(e["dst_symbol"] == "y.py::helper"
                   for e in store.edges_out("x.py::foo")), (
            f"x.py call edges were wiped by the removal-only reconcile ({res.summary()})")


def test_empty_index_with_bindings_aborts_pass(workspace):
    """A vanished index beside real bindings must refuse to reconcile, not
    mass-detach every binding."""
    codoc_dir = workspace  # NO lancedb table created — reads return []
    with open_store(codoc_dir) as store:
        f = Feature(title="Real feature")
        store.upsert_feature(f)
        store.upsert_binding(Binding(feature_id=f.id, file="x.py",
                                     symbol_path="x.py::foo", fingerprint="tok"))

    res = reconcile_drift(str(codoc_dir.parent), str(codoc_dir))

    assert res.auto == {} and res.proposed == []
    with open_store(codoc_dir) as store:
        assert store.binding_at("x.py", "x.py::foo") is not None, (
            "bindings were detached on an empty-index read")
