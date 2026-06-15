"""Cross-reference registry sidecar — ``tree.index.json``.

Pure derived state emitted on every loop pass (via ``write_sidecar``): every
feature, every binding, and every inline ``codoc:`` ref resolved/validated by the
leaf-matching rule that mirrors the IDE's navigation logic (``completion.ts:leaf``
/ ``extension.ts:openRef``). The registry must never touch ``tree.codoc`` text.
"""
from __future__ import annotations

import json

from codoc.codoc_file.diff import diff_codoc
from codoc.codoc_file.parse import parse_text
from codoc.codoc_file.render import (
    INDEX_FILENAME,
    render_tree,
    write_registry,
    write_sidecar,
    write_tree,
)
from codoc.model.binding import Binding
from codoc.model.feature import Feature
from codoc.store.db import open_store

import pytest


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def _populate(store):
    """Two features; ``auth`` owns a flat symbol + a NESTED symbol, ``util`` one."""
    auth = Feature(title="Authentication", description="Handles login and sessions.")
    util = Feature(title="Utilities", description="Shared helpers.")
    store.upsert_feature(auth)
    store.upsert_feature(util)

    bindings = [
        Binding(feature_id=auth.id, file="auth.py", symbol_path="auth.py::login", fingerprint="h1"),
        # A nested symbol: leaf "create" lives under "Session.create".
        Binding(feature_id=auth.id, file="session.py", symbol_path="session.py::Session.create", fingerprint="h2"),
        Binding(feature_id=util.id, file="utils.py", symbol_path="utils.py::helper", fingerprint="h3"),
    ]
    for b in bindings:
        store.upsert_binding(b)
    return auth, util


def _read_registry(tmp_path) -> dict:
    return json.loads((tmp_path / INDEX_FILENAME).read_text())


# ---------------------------------------------------------------------------
# Shape: features + bindings
# ---------------------------------------------------------------------------

def test_registry_written_by_write_sidecar(store, tmp_path):
    _populate(store)
    write_sidecar(store, tmp_path)
    assert (tmp_path / INDEX_FILENAME).exists()


def test_registry_written_by_write_tree(store, tmp_path):
    _populate(store)
    write_tree(store, tmp_path)
    assert (tmp_path / INDEX_FILENAME).exists()


def test_registry_lists_every_feature(store, tmp_path):
    auth, util = _populate(store)
    write_registry(store, tmp_path)
    reg = _read_registry(tmp_path)

    assert reg["version"] == 1
    assert reg["features"][auth.id] == {"title": "Authentication", "parent_id": None}
    assert reg["features"][util.id]["title"] == "Utilities"


def test_registry_preserves_parent(store, tmp_path):
    parent = Feature(title="Parent")
    store.upsert_feature(parent)
    child = Feature(title="Child", parent_id=parent.id)
    store.upsert_feature(child)
    write_registry(store, tmp_path)
    reg = _read_registry(tmp_path)
    assert reg["features"][child.id]["parent_id"] == parent.id


def test_registry_lists_every_binding_with_owner(store, tmp_path):
    auth, util = _populate(store)
    write_registry(store, tmp_path)
    reg = _read_registry(tmp_path)

    by_sym = {b["symbol_path"]: b for b in reg["bindings"]}
    assert by_sym["auth.py::login"]["feature_id"] == auth.id
    assert by_sym["auth.py::login"]["file"] == "auth.py"
    assert by_sym["session.py::Session.create"]["feature_id"] == auth.id
    assert by_sym["utils.py::helper"]["feature_id"] == util.id


# ---------------------------------------------------------------------------
# Resolution: leaf/suffix matching, NOT file::symbol equality
# ---------------------------------------------------------------------------

def test_leaf_ref_to_nested_binding_resolves(store, tmp_path):
    """A leaf-form ref (`create`) to `session.py::Session.create` → resolved."""
    feat = Feature(
        title="Session creation",
        description="Builds a session via [create](codoc:session.py#create).",
    )
    store.upsert_feature(feat)
    # bind the nested symbol so the ref has something to resolve against
    store.upsert_binding(Binding(
        feature_id=feat.id, file="session.py",
        symbol_path="session.py::Session.create", fingerprint="h"))
    write_registry(store, tmp_path)
    reg = _read_registry(tmp_path)

    ref = next(r for r in reg["refs"] if r["feature_id"] == feat.id)
    assert ref["file"] == "session.py" and ref["symbol"] == "create"
    assert ref["resolved"] is True


def test_qualified_ref_to_nested_binding_resolves(store, tmp_path):
    """A partial dotted ref (`Session.create`) also resolves (suffix match)."""
    feat = Feature(
        title="Session creation",
        description="See [it](codoc:session.py#Session.create).",
    )
    store.upsert_feature(feat)
    store.upsert_binding(Binding(
        feature_id=feat.id, file="session.py",
        symbol_path="session.py::Session.create", fingerprint="h"))
    write_registry(store, tmp_path)
    reg = _read_registry(tmp_path)
    ref = next(r for r in reg["refs"] if r["feature_id"] == feat.id)
    assert ref["resolved"] is True


def test_flat_symbol_ref_resolves(store, tmp_path):
    feat = Feature(
        title="Login flow",
        description="Entry at [login](codoc:auth.py#login).",
    )
    store.upsert_feature(feat)
    store.upsert_binding(Binding(
        feature_id=feat.id, file="auth.py",
        symbol_path="auth.py::login", fingerprint="h"))
    write_registry(store, tmp_path)
    reg = _read_registry(tmp_path)
    ref = next(r for r in reg["refs"] if r["feature_id"] == feat.id)
    assert ref["resolved"] is True


def test_ref_to_symbol_with_no_binding_is_unresolved(store, tmp_path):
    """The file exists in the index but carries no matching symbol → dead."""
    feat = Feature(
        title="Ghost ref",
        description="Points at [gone](codoc:auth.py#gone_symbol).",
    )
    store.upsert_feature(feat)
    store.upsert_binding(Binding(
        feature_id=feat.id, file="auth.py",
        symbol_path="auth.py::login", fingerprint="h"))
    write_registry(store, tmp_path)
    reg = _read_registry(tmp_path)
    ref = next(r for r in reg["refs"] if r["feature_id"] == feat.id)
    assert ref["resolved"] is False


def test_ref_to_unindexed_file_is_unresolved(store, tmp_path):
    feat = Feature(
        title="Wrong file",
        description="Points at [x](codoc:nowhere.py#login).",
    )
    store.upsert_feature(feat)
    store.upsert_binding(Binding(
        feature_id=feat.id, file="auth.py",
        symbol_path="auth.py::login", fingerprint="h"))
    write_registry(store, tmp_path)
    reg = _read_registry(tmp_path)
    ref = next(r for r in reg["refs"] if r["feature_id"] == feat.id)
    assert ref["resolved"] is False


def test_leaf_match_is_not_file_colon_symbol_equality(store, tmp_path):
    """Guards the central rule: a strict ``file::symbol`` equality check would
    mark this live nested ref dead — leaf-matching keeps it resolved."""
    feat = Feature(
        title="Nested",
        description="[c](codoc:session.py#create)",
    )
    store.upsert_feature(feat)
    store.upsert_binding(Binding(
        feature_id=feat.id, file="session.py",
        symbol_path="session.py::Session.create", fingerprint="h"))
    write_registry(store, tmp_path)
    reg = _read_registry(tmp_path)
    ref = next(r for r in reg["refs"] if r["feature_id"] == feat.id)
    # The naive comparison "session.py::create" != "session.py::Session.create"
    # would be False; leaf-matching makes it True.
    assert ref["resolved"] is True


# ---------------------------------------------------------------------------
# File-only refs (no #symbol)
# ---------------------------------------------------------------------------

def test_file_only_ref_resolves_when_file_indexed(store, tmp_path):
    feat = Feature(
        title="Module ref",
        description="The whole [auth module](codoc:auth.py).",
    )
    store.upsert_feature(feat)
    store.upsert_binding(Binding(
        feature_id=feat.id, file="auth.py",
        symbol_path="auth.py::login", fingerprint="h"))
    write_registry(store, tmp_path)
    reg = _read_registry(tmp_path)
    ref = next(r for r in reg["refs"] if r["feature_id"] == feat.id)
    assert ref["symbol"] is None
    assert ref["resolved"] is True


def test_file_only_ref_unresolved_when_file_absent(store, tmp_path):
    feat = Feature(
        title="Module ref",
        description="The whole [ghost module](codoc:ghost.py).",
    )
    store.upsert_feature(feat)
    write_registry(store, tmp_path)
    reg = _read_registry(tmp_path)
    ref = next(r for r in reg["refs"] if r["feature_id"] == feat.id)
    assert ref["symbol"] is None
    assert ref["resolved"] is False


# ---------------------------------------------------------------------------
# Edge cases + invariants
# ---------------------------------------------------------------------------

def test_empty_tree_writes_empty_registry(store, tmp_path):
    write_registry(store, tmp_path)
    assert (tmp_path / INDEX_FILENAME).exists()
    reg = _read_registry(tmp_path)
    assert reg == {"version": 1, "features": {}, "bindings": [], "refs": []}


def test_registry_does_not_touch_tree_codoc_roundtrip(store, tmp_path):
    """R8: writing the registry (via write_sidecar) leaves the text render→parse→
    diff a no-op; the registry is the only new artifact."""
    auth, _ = _populate(store)
    # a feature whose description carries both a live and a dead ref
    feat = Feature(
        title="Mixed refs",
        description="Live [login](codoc:auth.py#login), dead [x](codoc:auth.py#gone).",
    )
    store.upsert_feature(feat)

    text_before = render_tree(store)
    write_sidecar(store, tmp_path)  # emits both sidecar + registry
    text_after = render_tree(store)

    assert text_before == text_after
    parsed = parse_text(text_after)
    assert diff_codoc(parsed, store).is_empty()

    # the registry partitioned the two refs correctly
    reg = _read_registry(tmp_path)
    mixed = [r for r in reg["refs"] if r["feature_id"] == feat.id]
    by_sym = {r["symbol"]: r["resolved"] for r in mixed}
    assert by_sym == {"login": True, "gone": False}
