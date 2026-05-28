"""F1 — state-based drift reconcile: index-vs-bindings, idempotent recovery."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from codoc.loop.loop_a import _state_changeset
from codoc.model.binding import Binding
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _row(file, sym, tok="h"):
    return SimpleNamespace(file=file, symbol_path=sym, tokens_hash=tok, types_hash="t", source="src")


def _bind(store, fid, file, sym, fp):
    store.upsert_binding(Binding(feature_id=fid, file=file, symbol_path=sym, fingerprint=fp))


def test_unbound_chunk_is_added(store):
    rows = [_row("a.py", "a.py::foo", "h1")]
    cs = _state_changeset(rows, store, None)
    assert [c.symbol_path for c in cs.added] == ["a.py::foo"]
    assert not cs.modified and not cs.removed


def test_bound_matching_fingerprint_is_noop(store):
    f = Feature(title="F"); store.upsert_feature(f)
    _bind(store, f.id, "a.py", "a.py::foo", "h1")
    cs = _state_changeset([_row("a.py", "a.py::foo", "h1")], store, None)
    assert cs.is_empty()


def test_bound_changed_fingerprint_is_modified(store):
    f = Feature(title="F"); store.upsert_feature(f)
    _bind(store, f.id, "a.py", "a.py::foo", "old")
    cs = _state_changeset([_row("a.py", "a.py::foo", "new")], store, None)
    assert [c.symbol_path for c in cs.modified] == ["a.py::foo"]
    assert not cs.added and not cs.removed


def test_vanished_binding_is_removed(store):
    f = Feature(title="F"); store.upsert_feature(f)
    _bind(store, f.id, "a.py", "a.py::gone", "h")
    cs = _state_changeset([_row("a.py", "a.py::stays", "h")], store, None)  # ::gone not in index
    assert [c.symbol_path for c in cs.removed] == ["a.py::gone"]
    assert [c.symbol_path for c in cs.added] == ["a.py::stays"]


def test_file_scope_restricts_both_sides(store):
    f = Feature(title="F"); store.upsert_feature(f)
    _bind(store, f.id, "b.py", "b.py::gone", "h")  # outside scope → ignored
    rows = [_row("a.py", "a.py::foo", "h1"), _row("b.py", "b.py::bar", "h2")]
    cs = _state_changeset(rows, store, file_scope={"a.py"})
    assert [c.symbol_path for c in cs.added] == ["a.py::foo"]
    assert not cs.removed  # b.py::gone is out of scope, not flagged
