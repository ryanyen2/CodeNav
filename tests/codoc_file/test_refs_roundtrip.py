"""Phase 5 — refs render + sidecar round-trip tests."""
from __future__ import annotations

import json

import pytest

from codoc.codoc_file.diff import diff_codoc
from codoc.codoc_file.parse import parse_text
from codoc.codoc_file.render import (
    BINDINGS_FILENAME,
    _REFS_MAX_FILES,
    _REFS_MAX_PER_FILE,
    render_tree,
    write_tree,
)
from codoc.model.binding import Binding
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _populate(store):
    auth = Feature(title="Authentication", description="Handles login and session creation.")
    util = Feature(title="Utilities", description="Shared helpers.")
    store.upsert_feature(auth)
    store.upsert_feature(util)

    bindings_auth = [
        Binding(feature_id=auth.id, file="auth.py", symbol_path="auth.py::login", fingerprint="h1"),
        Binding(feature_id=auth.id, file="auth.py", symbol_path="auth.py::logout", fingerprint="h2"),
        Binding(feature_id=auth.id, file="session.py", symbol_path="session.py::create_session", fingerprint="h3"),
    ]
    bindings_util = [
        Binding(feature_id=util.id, file="utils.py", symbol_path="utils.py::helper", fingerprint="h4"),
    ]
    for b in bindings_auth + bindings_util:
        store.upsert_binding(b)

    return auth, util, bindings_auth, bindings_util


# ---------------------------------------------------------------------------
# Refs line render
# ---------------------------------------------------------------------------

def test_refs_line_appears_in_output(store):
    auth, util, _, _ = _populate(store)
    text = render_tree(store)
    assert "↪ refs:" in text


def test_refs_line_groups_by_file(store):
    """Refs group under the file (filename shown once), with leaf names listed."""
    auth, util, _, _ = _populate(store)
    text = render_tree(store)
    # File header appears, leaves listed without repeating "auth.py::" per symbol.
    assert "auth.py › " in text
    assert "login" in text and "logout" in text
    assert "auth.py::login" not in text  # no per-symbol filename repetition


def test_refs_line_per_file_symbol_overflow(store):
    """Many symbols in one file collapse to '+N' after the per-file cap."""
    feat = Feature(title="Big feature", description="Has many bindings.")
    store.upsert_feature(feat)
    extra = 3
    for i in range(_REFS_MAX_PER_FILE + extra):
        store.upsert_binding(
            Binding(feature_id=feat.id, file="big.py", symbol_path=f"big.py::fn{i}", fingerprint=f"h{i}")
        )
    text = render_tree(store)
    assert f"+{extra}" in text


def test_refs_line_file_overflow(store):
    """Many files collapse to '+N more files' after the file cap."""
    feat = Feature(title="Spread", description="Bindings across many files.")
    store.upsert_feature(feat)
    extra = 2
    for i in range(_REFS_MAX_FILES + extra):
        store.upsert_binding(
            Binding(feature_id=feat.id, file=f"f{i}.py", symbol_path=f"f{i}.py::fn", fingerprint=f"h{i}")
        )
    text = render_tree(store)
    assert f"+{extra} more file" in text


def test_module_chunk_rendered_cleanly(store):
    """A file::__module__ binding renders as ‹module›, not the raw dunder."""
    feat = Feature(title="Certs", description="CA bundle access.")
    store.upsert_feature(feat)
    store.upsert_binding(
        Binding(feature_id=feat.id, file="certs.py", symbol_path="certs.py::__module__", fingerprint="h")
    )
    text = render_tree(store)
    assert "‹module›" in text
    assert "__module__" not in text


def test_no_refs_line_for_unbound_feature(store):
    feat = Feature(title="Empty", description="No bindings.")
    store.upsert_feature(feat)
    text = render_tree(store)
    assert "↪ refs:" not in text


# ---------------------------------------------------------------------------
# Round-trip: render → parse → diff must be empty (refs lines are skipped)
# ---------------------------------------------------------------------------

def test_refs_roundtrip_no_diff(store):
    auth, util, _, _ = _populate(store)
    text = render_tree(store)
    parsed = parse_text(text)
    cd = diff_codoc(parsed, store)
    # refs lines must not pollute the description
    auth_node = next(n for n in parsed.nodes if n.id == auth.id)
    assert "↪ refs:" not in auth_node.description
    assert cd.user_ops == [] and cd.verdicts == []


def test_refs_not_captured_in_description(store):
    auth, _, _, _ = _populate(store)
    text = render_tree(store)
    parsed = parse_text(text)
    auth_node = next(n for n in parsed.nodes if n.id == auth.id)
    assert auth_node.description == "Handles login and session creation."


# ---------------------------------------------------------------------------
# Sidecar: tree.bindings.json
# ---------------------------------------------------------------------------

def test_sidecar_written_by_write_tree(store, tmp_path):
    _populate(store)
    write_tree(store, tmp_path)
    sidecar_path = tmp_path / BINDINGS_FILENAME
    assert sidecar_path.exists(), "tree.bindings.json was not written"


def test_sidecar_structure(store, tmp_path):
    auth, util, bindings_auth, bindings_util = _populate(store)
    write_tree(store, tmp_path)
    sidecar = json.loads((tmp_path / BINDINGS_FILENAME).read_text())

    assert sidecar["version"] == 1
    assert auth.id in sidecar["by_feature"]
    assert util.id in sidecar["by_feature"]

    auth_syms = {e["symbol"] for e in sidecar["by_feature"][auth.id]}
    assert "auth.py::login" in auth_syms
    assert "session.py::create_session" in auth_syms


def test_sidecar_by_file_index(store, tmp_path):
    auth, util, _, _ = _populate(store)
    write_tree(store, tmp_path)
    sidecar = json.loads((tmp_path / BINDINGS_FILENAME).read_text())

    assert "auth.py" in sidecar["by_file"]
    auth_file_entries = sidecar["by_file"]["auth.py"]
    syms = {e["symbol"] for e in auth_file_entries}
    assert "auth.py::login" in syms
    assert "auth.py::logout" in syms
    fids = {e["feature_id"] for e in auth_file_entries}
    assert auth.id in fids


def test_sidecar_features_meta(store, tmp_path):
    auth, util, _, _ = _populate(store)
    write_tree(store, tmp_path)
    sidecar = json.loads((tmp_path / BINDINGS_FILENAME).read_text())

    assert auth.id in sidecar["features"]
    assert sidecar["features"][auth.id]["title"] == "Authentication"
    assert sidecar["features"][auth.id]["parent_id"] is None
