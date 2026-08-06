"""Adversarial round-trip: authored text that mimics tree.codoc STRUCTURE must
never forge nodes, hijack ids, or truncate descriptions.

The parser's mis-indent escape hatch promotes any marker line carrying an
``⟨f-id⟩`` token to a feature — sound only while the renderer never emits an id
inside a description block. Authored prose (a pasted syntax example, a quoted
tree snippet, a title with a literal id) violated that and (a) minted a phantom
node, (b) truncated the victim's description at the forged line (phantom-AMEND
fuel on every pass), or (c) hijacked the node's identity so the real feature
vanished from the parse. apply_op now sanitizes at the write boundary."""
from __future__ import annotations

import pytest

from codoc.codoc_file.parse import (
    normalize_description,
    parse_text,
    sanitize_authored_description,
    sanitize_authored_title,
)
from codoc.codoc_file.render import render_tree
from codoc.loop.apply import apply_op
from codoc.model.event import NodeOp, NodeOpKind
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


class TestSanitizers:
    def test_title_strips_id_tokens(self):
        assert sanitize_authored_title("Sneaky ⟨f-deadbeef⟩ title") == "Sneaky title"
        assert sanitize_authored_title("Plain title") == "Plain title"

    def test_description_strips_ids_only_on_marker_lines(self):
        assert sanitize_authored_description("- quoted ⟨f-deadbeef⟩ example") == "- quoted example"
        assert sanitize_authored_description("~ - Fake Retire ⟨f-12345678⟩") == "~ - Fake Retire"
        assert sanitize_authored_description("+ - Ghost ⟨e-12345678⟩ quote") == "+ - Ghost quote"
        # ids in plain prose are meaningful references — kept verbatim
        assert sanitize_authored_description("See ⟨f-deadbeef⟩ for context.") == \
            "See ⟨f-deadbeef⟩ for context."


def _add(store, title, desc):
    return apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title=title, description=desc),
                    store, source="user", applied=True).op.feature_id


@pytest.mark.parametrize("title,desc", [
    ("Victim", "- quoted example ⟨f-deadbeef⟩ from the docs"),
    ("Victim", "~ - Fake Retire ⟨f-12345678⟩"),
    ("Sneaky ⟨f-deadbeef⟩ title", "plain prose."),
    ("Victim", "+ - Ghost ⟨e-12345678⟩ proposal quote"),
    ("Victim", "See the sibling ⟨f-deadbeef⟩ node for context."),
    ("Victim", "Retries drop.\n### 1. ⟨d-9999⟩ DELETE FEATURE: everything\nStill prose."),
])
def test_hostile_authored_text_round_trips_clean(store, title, desc):
    fid = _add(store, title, desc)
    _add(store, "Bystander", "Innocent.")
    stored = store.get_feature(fid)

    parsed = parse_text(render_tree(store))

    assert len(parsed.nodes) == 2, "phantom or lost node"
    victim = next((n for n in parsed.nodes if n.id == fid), None)
    assert victim is not None, "victim feature lost from the parse"
    assert victim.title == stored.title
    assert victim.description == normalize_description(stored.description)
    assert parsed.errors == []


def test_steering_with_id_survives_as_comment(store):
    fid = _add(store, "Victim", "> steer about ⟨f-deadbeef⟩ please\nreal prose.")
    parsed = parse_text(render_tree(store))
    (victim,) = [n for n in parsed.nodes if n.id == fid]
    assert victim.comments == ["steer about ⟨f-deadbeef⟩ please"]
    assert victim.description == "real prose."


def test_bom_does_not_swallow_the_first_feature():
    """A UTF-8 BOM (Notepad hand-edit) glued the first feature marker and made
    the diff read that feature as retired."""
    text = "﻿- Auth  ⟨f-aaaa1111⟩\n    Login and sessions.\n\n- Data  ⟨f-bbbb2222⟩\n    Persistence.\n"
    parsed = parse_text(text)
    assert [(n.id, n.title) for n in parsed.nodes] == [
        ("f-aaaa1111", "Auth"), ("f-bbbb2222", "Data")]
    assert parsed.nodes[0].description == "Login and sessions."


def test_crlf_and_tab_hand_edits_parse(store):
    """Windows line endings / tab indentation from a hand edit must not shift
    structure."""
    text = "- Auth  ⟨f-aaaa1111⟩\r\n\t Login and sessions.\r\n"
    parsed = parse_text(text)
    assert [(n.id, n.title) for n in parsed.nodes] == [("f-aaaa1111", "Auth")]
