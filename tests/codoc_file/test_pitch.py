"""Derived one-line pitch slice (sidecar v5).

The pitch is pure derivation in :func:`codoc.codoc_file.render._pitch` — the first
sentence of a feature's description (inline ``codoc:`` refs flattened to their
labels), trimmed to :data:`PITCH_MAX_LEN`, falling back to the title when the
description is empty/blank or the first sentence is empty after flattening (e.g.
it was only a citation). No LLM call, no model column, no tree.codoc text change.
"""
from __future__ import annotations

import json

import pytest

from codoc.codoc_file.render import (
    BINDINGS_FILENAME,
    PITCH_MAX_LEN,
    _pitch,
    write_tree,
)
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# _pitch helper (unit)
# ---------------------------------------------------------------------------

def test_first_sentence_of_multi_sentence():
    desc = "Handles login and session creation. It also rotates tokens nightly."
    assert _pitch(desc, "Auth") == "Handles login and session creation."


def test_empty_description_falls_back_to_title():
    assert _pitch("", "Authentication") == "Authentication"
    assert _pitch("   \n  ", "Authentication") == "Authentication"


def test_multi_paragraph_takes_only_first_sentence():
    desc = (
        "Validates credentials against the user store. Returns a session token.\n"
        "\n"
        "A second paragraph that should never appear in the pitch."
    )
    assert _pitch(desc, "Auth") == "Validates credentials against the user store."


def test_citation_leading_description_flattens_to_label():
    # The description leads with a codoc ref; the pitch must be readable prose
    # (the link label), never raw markdown.
    desc = "See [login handler](codoc:auth.py#login) for the entry point."
    pitch = _pitch(desc, "Auth")
    assert pitch == "See login handler for the entry point."
    assert "codoc:" not in pitch and "](" not in pitch


def test_first_sentence_that_is_only_a_citation_falls_back_to_title():
    # After flattening, the (empty-label) ref leaves an empty first sentence.
    desc = "[](codoc:auth.py#login)"
    assert _pitch(desc, "Authentication") == "Authentication"


def test_over_long_first_sentence_is_trimmed():
    long_sentence = "x" * (PITCH_MAX_LEN + 50) + "."
    pitch = _pitch(long_sentence, "Title")
    assert len(pitch) <= PITCH_MAX_LEN
    assert pitch == "x" * PITCH_MAX_LEN


def test_no_sentence_boundary_uses_whole_first_line_trimmed():
    desc = "A description with no terminal punctuation"
    assert _pitch(desc, "Title") == "A description with no terminal punctuation"


# ---------------------------------------------------------------------------
# Sidecar (integration) — pitch is emitted; version bumped to 5
# ---------------------------------------------------------------------------

def test_sidecar_emits_pitch_and_bumps_version(store, tmp_path):
    multi = Feature(
        title="Authentication",
        description="Handles login and session creation. It also rotates tokens.",
    )
    empty = Feature(title="Utilities", description="")
    cited = Feature(
        title="Routing",
        description="Dispatches to [the router](codoc:app.py#route) by path.",
    )
    for f in (multi, empty, cited):
        store.upsert_feature(f)

    write_tree(store, tmp_path)
    sidecar = json.loads((tmp_path / BINDINGS_FILENAME).read_text())

    assert sidecar["version"] == 5
    feats = sidecar["features"]
    assert feats[multi.id]["pitch"] == "Handles login and session creation."
    assert feats[empty.id]["pitch"] == "Utilities"  # fallback to title
    assert feats[cited.id]["pitch"] == "Dispatches to the router by path."
    # existing per-feature meta fields stay intact
    assert feats[multi.id]["title"] == "Authentication"
    assert feats[multi.id]["realized"] is True
    assert "parent_id" in feats[multi.id]
