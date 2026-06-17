"""U7 — description blank-line normalization (TS↔Python parity).

The rich editor round-trips descriptions through a paragraph model that has no
notion of multiple consecutive blank lines, so ``parse.py`` collapses interior
runs of blank lines to a SINGLE break (and drops leading/trailing blanks) when
building the description string — matching ``doc-serialize`` / ``tree-model`` so a
raw-text edit with extra blanks doesn't reflow on the next render.
"""
from __future__ import annotations

from codoc.codoc_file.parse import parse_text
from codoc.codoc_file.render import render_tree
from codoc.model.feature import Feature
from codoc.store.db import open_store


def _desc(text: str) -> str:
    tree = parse_text(text)
    assert len(tree.nodes) == 1, tree.errors
    return tree.nodes[0].description


def test_single_blank_line_is_a_paragraph_break_kept():
    text = "- F  ⟨f-1⟩\n    P1.\n\n    P2.\n"
    assert _desc(text) == "P1.\n\nP2."


def test_multiple_blank_lines_collapse_to_one():
    text = "- F  ⟨f-1⟩\n    P1.\n\n\n\n    P2.\n"  # 3 blank lines between
    assert _desc(text) == "P1.\n\nP2."            # collapsed to a single break


def test_leading_and_trailing_blanks_dropped():
    text = "- F  ⟨f-1⟩\n\n    Only line.\n\n\n"
    assert _desc(text) == "Only line."


def test_single_paragraph_unaffected():
    text = "- F  ⟨f-1⟩\n    Just one line.\n"
    assert _desc(text) == "Just one line."


def test_render_reparse_is_a_fixpoint_after_collapse(tmp_path):
    """A description stored from collapsed text renders, and re-parsing the render
    yields the same string — the normalization is stable end-to-end."""
    cd = tmp_path / ".codoc"
    cd.mkdir()
    s = open_store(str(cd))
    try:
        # The store description is already the collapsed form (what a settle stores).
        f = Feature(title="F", description="P1.\n\nP2.")
        s.upsert_feature(f)
        rendered = render_tree(s)
    finally:
        s.close()
    # Re-parsing the canonical render reproduces the same description (fixpoint).
    assert _desc(rendered) == "P1.\n\nP2."
