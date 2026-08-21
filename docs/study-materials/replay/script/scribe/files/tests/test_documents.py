"""The three sample documents, end to end.

Each one exists to exercise something the others cannot. The report has
furniture, footnotes and numbered headings; the memo has none of those and is
the document that makes "keep the running header" a real alternative; the
handbook has deep numbering and a numbered list that must not become headings.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scribe.convert import convert

FIXTURES = Path(__file__).parent.parent / "fixtures"


def md(name: str) -> str:
    return convert((FIXTURES / f"{name}.txt").read_text(encoding="utf-8")).markdown


# ── the report ───────────────────────────────────────────────────────────────

def test_the_running_header_is_gone_from_the_report():
    assert "Marine Institute" not in md("report")


def test_the_page_numbers_are_gone():
    assert "- 1 -" not in md("report")


def test_a_word_split_across_a_line_break_is_whole_again():
    assert "photogrammetric" in md("report")


def test_a_word_split_across_a_page_is_handled_the_same_way():
    assert "settlement had moved" in md("report")


def test_headings_carry_their_depth():
    out = md("report")
    assert "## Summary" in out
    assert "### Sites" in out


def test_footnote_markers_are_rewritten_where_they_stand():
    # The markers are rewritten during the reflow now, in the same pass that
    # builds the paragraph, so the reference sits in the sentence it belongs to.
    out = md("report")
    assert "comparable.[^1]" in out


def test_decimals_survive():
    # They did not. Every decimal became a footnote reference, and every test
    # passed because none of them contained a number.
    out = md("report")
    assert "0.8 metres per year" in out
    assert "0.5 in the 2019 baseline" in out


def test_bullets_are_a_tight_list():
    assert "- Ardmore, revetted in 2021\n- Blackrock, unprotected" in md("report")


# ── the memo ─────────────────────────────────────────────────────────────────

def test_a_two_page_memo_keeps_its_first_line():
    # Nothing repeats, so nothing is furniture. If the rule fired here it would
    # eat the title of every short document.
    assert "Note on the filing backlog" in md("memo")


def test_ligatures_are_normalised():
    out = md("memo")
    assert "ﬁ" not in out
    assert "filing" in out and "staffing" in out


def test_the_memo_has_no_headings():
    # It numbers nothing, so the numbering rule finds nothing. That is the cost
    # of choosing numbering over a length heuristic, and it is deliberate.
    assert "#" not in md("memo")


# ── the handbook ─────────────────────────────────────────────────────────────

def test_deep_numbering_becomes_deep_headings():
    assert "#### What counts as working alone" in md("handbook")


def test_a_numbered_list_does_not_become_headings():
    out = md("handbook")
    assert "Entering water above the knee" in out
    assert "# Entering water" not in out


def test_the_bullet_character_is_recognised():
    out = md("handbook")
    assert "- A charged radio, not only a phone" in out


def test_a_paragraph_broken_across_a_page_is_rejoined():
    assert "two people at opposite ends of a beach are both working alone" in md("handbook").lower()


# ── every document ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["report", "memo", "handbook"])
def test_no_document_ends_up_empty(name):
    assert len(md(name).strip()) > 200


@pytest.mark.parametrize("name", ["report", "memo", "handbook"])
def test_no_form_feed_survives(name):
    assert "\f" not in md(name)


@pytest.mark.parametrize("name", ["report", "memo", "handbook"])
def test_no_three_blank_lines_in_a_row(name):
    assert "\n\n\n" not in md(name)


@pytest.mark.parametrize("name", ["report", "memo", "handbook"])
def test_converting_twice_gives_the_same_thing(name):
    raw = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
    assert convert(raw).markdown == convert(raw).markdown
