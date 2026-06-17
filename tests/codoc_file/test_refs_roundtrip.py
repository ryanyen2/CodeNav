"""Bindings sidecar + inline markdown refs.

Derived bindings are no longer printed into tree.codoc (no more ``↪ refs:`` line);
they ride in tree.bindings.json for the IDE to render as inlay chips. Authored
code citations use inline markdown links — ``[label](codoc:file#symbol)`` — which
stay verbatim in the description so the round-trip is exact.
"""
from __future__ import annotations

import json

import pytest

from codoc.codoc_file.diff import diff_codoc
from codoc.codoc_file.parse import extract_refs, parse_text
from codoc.codoc_file.render import BINDINGS_FILENAME, render_tree, write_tree
from codoc.model.binding import Binding
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


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
# Derived bindings stay out of the text
# ---------------------------------------------------------------------------

def test_no_refs_line_in_text(store):
    _populate(store)
    text = render_tree(store)
    assert "↪ refs:" not in text
    assert "›" not in text
    # raw symbol paths never leak into the human-facing tree
    assert "auth.py::login" not in text


def test_derived_bindings_roundtrip_noop(store):
    auth, _, _, _ = _populate(store)
    text = render_tree(store)
    parsed = parse_text(text)
    auth_node = next(n for n in parsed.nodes if n.id == auth.id)
    assert auth_node.description == "Handles login and session creation."
    assert diff_codoc(parsed, store).is_empty()


# ---------------------------------------------------------------------------
# Inline markdown refs
# ---------------------------------------------------------------------------

def test_inline_ref_preserved_and_extracted(store):
    feat = Feature(
        title="Default CA bundle lookup",
        description="Points TLS verification at the bundled store via "
                    "[where](codoc:certs.py#where_to_bundle), with override support.",
    )
    store.upsert_feature(feat)

    text = render_tree(store)
    assert "[where](codoc:certs.py#where_to_bundle)" in text

    parsed = parse_text(text)
    node = next(n for n in parsed.nodes if n.id == feat.id)
    # description (with the link) round-trips exactly → no spurious edit
    assert node.description == feat.description
    assert diff_codoc(parsed, store).is_empty()

    refs = node.refs
    assert len(refs) == 1
    assert refs[0].file == "certs.py" and refs[0].symbol == "where_to_bundle" and refs[0].label == "where"


def test_extract_refs_handles_no_symbol():
    refs = extract_refs("see [models](codoc:models.py) and [get](codoc:api.py#get)")
    assert (refs[0].file, refs[0].symbol) == ("models.py", None)
    assert (refs[1].file, refs[1].symbol) == ("api.py", "get")


# ---------------------------------------------------------------------------
# Sidecar: tree.bindings.json (unchanged contract)
# ---------------------------------------------------------------------------

def test_sidecar_written_by_write_tree(store, tmp_path):
    _populate(store)
    write_tree(store, tmp_path)
    assert (tmp_path / BINDINGS_FILENAME).exists()


def test_sidecar_structure(store, tmp_path):
    auth, util, _, _ = _populate(store)
    write_tree(store, tmp_path)
    sidecar = json.loads((tmp_path / BINDINGS_FILENAME).read_text())

    assert sidecar["version"] == 5
    assert "proposals" in sidecar and "realized" in sidecar["features"][auth.id]
    assert auth.id in sidecar["by_feature"] and util.id in sidecar["by_feature"]
    auth_syms = {e["symbol"] for e in sidecar["by_feature"][auth.id]}
    assert "auth.py::login" in auth_syms and "session.py::create_session" in auth_syms


def test_sidecar_v4_changes_feed_and_holds(store, tmp_path):
    """v4: a provenance feed of recent applied events + the doc-wins hold set."""
    from codoc.loop.apply import apply_op
    from codoc.loop import edits as edits_channel
    from codoc.model.event import NodeOp, NodeOpKind

    auth, _, _, _ = _populate(store)
    apply_op(NodeOp(kind=NodeOpKind.AMEND, feature_id=auth.id, description="Reworded."),
             store, source="loop_a_agent", applied=True,
             actor="claude-code", mode="auto", caused_by="d-12345678")
    edits_channel._write_edits_file(
        tmp_path, edits=[],
        intents=[{"id": "d-x", "feature_id": auth.id, "actor": "human", "ts": 0}])
    write_tree(store, tmp_path)
    sidecar = json.loads((tmp_path / BINDINGS_FILENAME).read_text())

    feed = sidecar["changes"]
    amends = [c for c in feed if c["kind"] == "amend"]
    assert amends and amends[0]["feature_id"] == auth.id
    assert amends[0]["actor"] == "claude-code"
    assert amends[0]["mode"] == "auto"
    assert amends[0]["caused_by"] == "d-12345678"
    assert sidecar["holds"] == [auth.id]


def test_sidecar_hold_detail_from_manifest(store, tmp_path):
    """v5: each held feature with a QUEUED directive carries {kind, intent} so the IDE
    can show WHAT codoc understood (the pending-intent rail's hover title), not just a
    count. A feature held only by a live intent (no directive) is absent from the
    detail map — it still gets the plain rail via `holds`."""
    from codoc.loop import edits as edits_channel
    from codoc.loop.filenames import REALIZE_FILENAME

    auth, util, _, _ = _populate(store)
    # Queue an AMEND directive for `auth`; realize.md must exist beside the manifest
    # or read_manifest treats it as stale.
    edits_channel.write_manifest(tmp_path, [
        edits_channel.Directive(id="d-aaaa1111", feature_id=auth.id, kind="amend",
                                caused_by="e-1", text="UPDATE FEATURE: ...")])
    (tmp_path / REALIZE_FILENAME).write_text("### 1. ⟨d-aaaa1111⟩ UPDATE FEATURE: ...\n")
    # `util` is held only by a live intent (no directive) → rail but no gloss.
    edits_channel._write_edits_file(
        tmp_path, edits=[],
        intents=[{"id": "d-u", "feature_id": util.id, "actor": "human", "ts": 0}])
    write_tree(store, tmp_path)
    sidecar = json.loads((tmp_path / BINDINGS_FILENAME).read_text())

    assert set(sidecar["holds"]) == {auth.id, util.id}      # both held
    detail = sidecar["hold_detail"]
    assert auth.id in detail and util.id not in detail       # only the directive carries detail
    assert detail[auth.id]["kind"] == "amend"
    assert detail[auth.id]["intent"]                         # a non-empty plain-language gloss


def test_sidecar_by_file_index(store, tmp_path):
    auth, _, _, _ = _populate(store)
    write_tree(store, tmp_path)
    sidecar = json.loads((tmp_path / BINDINGS_FILENAME).read_text())

    assert "auth.py" in sidecar["by_file"]
    syms = {e["symbol"] for e in sidecar["by_file"]["auth.py"]}
    assert {"auth.py::login", "auth.py::logout"} <= syms
    assert auth.id in {e["feature_id"] for e in sidecar["by_file"]["auth.py"]}


def test_sidecar_features_meta(store, tmp_path):
    auth, _, _, _ = _populate(store)
    write_tree(store, tmp_path)
    sidecar = json.loads((tmp_path / BINDINGS_FILENAME).read_text())
    assert sidecar["features"][auth.id]["title"] == "Authentication"
    assert sidecar["features"][auth.id]["parent_id"] is None
