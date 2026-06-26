"""U1 / R19 — the tree.doc.json↔store↔tree.codoc round-trip is idempotent.

A description that differs only by trailing whitespace (or a trailing newline) must
not be read as a new edit. Before this fix, ``parse.py`` stripped per-line trailing
whitespace while ``doc_parse.py`` and ``render.py`` preserved it, so a doc edit that
carried trailing whitespace produced a phantom AMEND every pass — the daemon
re-applied and re-rendered in a loop (the field repro: 4× identical "queued"
log lines). The canonical normalization (``parse.normalize_description``) is now
applied at both parsers and the diff comparison, so the round-trip converges.

Covers AE6 (one applied event per real edit, not a repeating sequence).
"""
from __future__ import annotations

import json

import pytest

from codoc.codoc_file.diff import diff_codoc
from codoc.codoc_file.doc_parse import parse_doc
from codoc.codoc_file.parse import normalize_description, parse_tree_file
from codoc.codoc_file.render import write_tree
from codoc.loop.apply import apply_op
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def codoc_dir(tmp_path):
    (tmp_path / ".codoc").mkdir(parents=True, exist_ok=True)
    return tmp_path  # render/parse take the dir containing tree.codoc


def _doc_with_description(fid: str, title: str, paragraph_text: str) -> dict:
    return {
        "type": "doc",
        "content": [
            {"type": "featureHeading",
             "attrs": {"fid": fid, "level": 0, "retired": False, "realized": True},
             "content": [{"type": "text", "text": title}]},
            {"type": "paragraph", "content": [{"type": "text", "text": paragraph_text}]},
        ],
    }


# ── the canonical normalization itself ───────────────────────────────────────

def test_normalize_description_strips_per_line_and_edges():
    assert normalize_description("Holds brand colors. ") == "Holds brand colors."
    assert normalize_description("Holds brand colors.\n") == "Holds brand colors."
    assert normalize_description("  leading and trailing  ") == "leading and trailing"
    # interior blank-run collapse + edge-blank drop (matches parse.py flush_desc)
    assert normalize_description("a.\n\n\n\nb.") == "a.\n\nb."
    assert normalize_description("\n\nonly.\n\n") == "only."
    # already-canonical text is a fixed point
    canon = "First paragraph.\n\nSecond paragraph."
    assert normalize_description(canon) == canon


# ── doc_parse matches the canonical form (was: preserved trailing whitespace) ─

def test_doc_parse_normalizes_trailing_whitespace():
    tree = parse_doc(_doc_with_description("f-a", "X", "Holds brand colors. "))
    assert tree.nodes[0].description == "Holds brand colors."


# ── the production phantom: a doc edit with trailing whitespace → text round-trip

def test_doc_edit_with_trailing_whitespace_round_trips_clean(codoc_dir):
    """doc.json (trailing ws) → store → render tree.codoc → re-parse → diff is empty."""
    with open_store(codoc_dir) as s:
        f = Feature(title="X", description="seed")
        s.upsert_feature(f)
        # apply the doc edit (description carries a trailing space, as the webview may emit)
        parsed = parse_doc(_doc_with_description(f.id, "X", "Holds brand colors. "))
        for op in diff_codoc(parsed, s).user_ops:
            apply_op(op, s, source="user", applied=True)
        write_tree(s, codoc_dir)
        # the text round-trip (what reconcile.has_pending_user_edits checks) must be empty
        assert diff_codoc(parse_tree_file(codoc_dir), s).is_empty()


def test_phantom_diff_from_noncanonical_store_text(codoc_dir):
    """Even a store description set by a non-parser path (agent/bootstrap) with trailing
    whitespace must not phantom-diff against the canonical re-parse."""
    with open_store(codoc_dir) as s:
        s.upsert_feature(Feature(title="X", description="Holds brand colors. "))  # direct, non-canonical
        write_tree(s, codoc_dir)
        assert diff_codoc(parse_tree_file(codoc_dir), s).is_empty()


def test_ae6_second_pass_is_a_noop(codoc_dir):
    """Covers AE6 — after a doc edit is applied, re-diffing the same doc yields no ops
    (one applied event per edit, not a repeating sequence)."""
    with open_store(codoc_dir) as s:
        f = Feature(title="X", description="seed")
        s.upsert_feature(f)
        doc = _doc_with_description(f.id, "X", "Should also cache the palette lookups. ")
        first = diff_codoc(parse_doc(doc), s)
        assert not first.is_empty()  # the real edit
        for op in first.user_ops:
            apply_op(op, s, source="user", applied=True)
        # second pass over the SAME doc (host re-persist) must be a no-op
        assert diff_codoc(parse_doc(doc), s).is_empty()


def test_build_doc_from_store_is_idempotent(codoc_dir):
    """AE1 prerequisite — projecting an unchanged store twice is byte-identical."""
    from codoc.codoc_file.doc_render import build_doc_from_store

    with open_store(codoc_dir) as s:
        s.upsert_feature(Feature(title="Auth", description="Login and sessions.", local_id="lid-1"))
        s.upsert_feature(Feature(title="Billing", description="Charges and invoices."))
        first = build_doc_from_store(s)
        second = build_doc_from_store(s)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
