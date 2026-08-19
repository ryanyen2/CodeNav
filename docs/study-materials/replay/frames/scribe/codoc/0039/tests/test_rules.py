"""Each rule, on its own.

One test per policy, plus the cases where two policies meet. The interesting
ones are at the bottom: those are where a change to one rule breaks another.
"""
from __future__ import annotations

import pytest

from scribe import blocks, furniture, notes, paragraphs, text
from scribe.lines import read
from scribe.settings import Settings


# ── joining words split across a line break ──────────────────────────────────

def test_a_typeset_hyphen_is_dropped():
    assert paragraphs.dehyphenate("settle-", "ment had moved") == ("settlement", "had moved")


def test_a_real_compound_keeps_its_hyphen_when_a_document_asks():
    # "well-being" split across lines is not "wellbeing", and nothing in the
    # text says which was meant, so the prefixes are listed rather than guessed.
    # Listed by the document, now: the default list is empty.
    asked = Settings(keep_hyphen=frozenset({"well"}))
    assert paragraphs.dehyphenate("well-", "being matters", asked) == ("well-being", "matters")


def test_and_by_default_it_does_not():
    # The price of an empty default, stated plainly. Every hyphen a document
    # keeps is one it asked for, and this is what not asking looks like.
    assert paragraphs.dehyphenate("well-", "being matters") == ("wellbeing", "matters")


def test_a_dash_before_a_number_is_not_a_broken_word():
    assert paragraphs.dehyphenate("see figure 3-", "1 below") is None


# ── when a newline ends the paragraph ────────────────────────────────────────

def test_a_single_newline_continues_the_paragraph():
    assert paragraphs.is_break("between March", "and September.") is False


def test_a_blank_line_always_breaks():
    assert paragraphs.is_break("the line.", "") is True


def test_a_short_line_ending_a_sentence_breaks():
    # Without this the last line of every paragraph glues onto the first line of
    # the next, which is the most visible failure in the output.
    assert paragraphs.is_break("held the line.", "2. Method") is True


# ── headings ─────────────────────────────────────────────────────────────────

def test_numbering_gives_the_depth():
    assert blocks.heading_level("3.1.4 What counts as working alone") == (4, "What counts as working alone")


def test_a_long_numbered_line_is_a_list_item_not_a_heading():
    long = "1. Entering water above the knee, whether or not you are wearing a buoyancy aid"
    assert blocks.heading_level(long) is None


def test_an_unnumbered_line_is_not_a_heading():
    # The alternative rule — short line, no full stop — was tried first and
    # promoted every table caption and every name in a list.
    assert blocks.heading_level("Summary") is None


# ── bullets ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line", ["- Blackrock", "* Blackrock", "• Blackrock"])
def test_the_three_bullet_marks(line):
    assert blocks.bullet(line) == "Blackrock"


def test_a_dash_with_no_space_is_prose():
    assert blocks.bullet("-3 degrees overnight") is None


# ── page furniture ───────────────────────────────────────────────────────────

def page(*, top: str = "", body: str = "body", bottom: str = "") -> str:
    """A page with a real margin.

    The furniture rules only look near an edge, and on a four line page every
    line is near an edge. Building pages this way keeps the tests about the rule
    rather than about the fixture, which the first draft of these was not.
    """
    before = [f"filler {i}" for i in range(3)]
    after = [f"filler {i}" for i in range(3, 6)]
    return "\n".join([top, ""] + before + [body] + after + ["", bottom]) + "\n"


def test_a_line_on_every_page_near_the_top_is_furniture():
    raw = "\f".join(page(top="Handbook", body=f"real text {n}") for n in ("one", "two", "three"))
    doc = furniture.strip(read(raw))
    assert "Handbook" not in "\n".join(line.text for line in doc.lines)
    assert "real text two" in "\n".join(line.text for line in doc.lines)


def test_a_two_page_document_has_no_furniture():
    # Under three pages there is no pattern to establish, and a two page letter
    # whose first line echoes its last would otherwise lose both.
    raw = "\f".join(page(top="Note", body=b) for b in ("body", "more"))
    doc = furniture.strip(read(raw))
    assert "Note" in "\n".join(line.text for line in doc.lines)


def test_a_running_header_with_a_changing_number_still_counts():
    raw = "\f".join(page(top=f"Report page {n}", body=f"unique text {n}") for n in (1, 2, 3))
    doc = furniture.strip(read(raw))
    assert "Report page" not in "\n".join(line.text for line in doc.lines)


def test_a_bare_number_at_the_foot_is_a_page_number():
    doc = read(page(bottom="- 3 -"))
    number = next(line for line in doc.lines if line.text == "- 3 -")
    assert furniture.is_page_number(number)


def test_a_number_in_the_middle_of_a_page_is_not():
    doc = read("\n".join(["body"] * 5 + ["7"] + [f"line {i}" for i in range(10)]) + "\n")
    seven = next(line for line in doc.lines if line.text == "7")
    assert not furniture.is_page_number(seven)


# ── footnotes ────────────────────────────────────────────────────────────────

def test_a_numbered_line_at_the_foot_is_a_note():
    assert notes.looks_like_note("1 The 2019 report describes the method.", from_bottom=2)


def test_the_same_line_in_the_middle_of_a_page_is_not():
    assert not notes.looks_like_note("1 The 2019 report describes the method.", from_bottom=30)


def test_a_marker_welded_to_a_word_becomes_a_reference():
    assert notes.mark("directly comparable.1") == "directly comparable.[^1]"


def test_a_decimal_is_not_a_footnote_marker():
    # It was. Every decimal in the report came out as a reference, and the tests
    # all passed because none of them had a number in them.
    assert notes.mark("was 0.8 metres per year") == "was 0.8 metres per year"


def test_a_year_is_not_a_footnote_marker():
    assert notes.mark("in the 2019 baseline") == "in the 2019 baseline"


# ── characters ───────────────────────────────────────────────────────────────

def test_ligatures_and_quotes_are_normalised():
    assert text.normalise("the ﬁling ‘backlog’") == "the filing 'backlog'"


# ── blank lines ──────────────────────────────────────────────────────────────

def test_runs_of_blank_lines_become_one():
    assert blocks.collapse_blanks(["a", "", "", "", "b"]) == ["a", "", "b"]


def test_leading_and_trailing_blanks_go():
    assert blocks.collapse_blanks(["", "a", ""]) == ["a"]


# ── where two rules meet ─────────────────────────────────────────────────────

def test_furniture_runs_before_headings_and_that_is_load_bearing():
    """A running header that is also a section title.

    The furniture rule takes it out first, so the heading rule never sees it. If
    the order were reversed, "Section 3" would be promoted to a heading on every
    page of the document. Anybody changing either rule has to know this.
    """
    raw = "\f".join(page(top="3. Section 3", body=f"unique text {n}") for n in (1, 2, 3))
    doc = furniture.strip(read(raw))
    remaining = [line.text for line in doc.lines if line.text.strip()]
    assert "3. Section 3" not in remaining
    assert [t for t in remaining if t.startswith("unique")] == [
        "unique text 1", "unique text 2", "unique text 3"]
    # And the same line, appearing once, is still a heading.
    assert blocks.heading_level("3. Section 3") == (2, "Section 3")
