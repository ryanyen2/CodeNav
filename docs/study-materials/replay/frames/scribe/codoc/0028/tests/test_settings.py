"""The config file, and the settings the rules read.

Two things are being pinned here. First, that the defaults are exactly what the
rules used to hard-code, because that is the promise that lets every other test
in this suite go on calling the rules with no settings at all. Second, that a
config file which says something wrong says so loudly, since a key that is
quietly ignored looks like a broken rule rather than a typo.
"""
from __future__ import annotations

import pytest

from scribe import blocks, furniture, notes, paragraphs, text
from scribe.convert import convert
from scribe.lines import read
from scribe.settings import DEFAULTS, ConfigError, Settings, find, load


def write(directory, body: str, name: str = "scribe.toml"):
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


# ── the defaults are the old constants ───────────────────────────────────────

def test_the_defaults_are_the_values_the_rules_used_to_hard_code():
    assert (DEFAULTS.edge, DEFAULTS.min_pages) == (2, 3)
    assert DEFAULTS.short_line == 60
    assert DEFAULTS.max_heading_words == 12
    assert DEFAULTS.note_depth == 6
    assert DEFAULTS.normalise_characters is True
    assert "well" in DEFAULTS.keep_hyphen and "part" not in DEFAULTS.keep_hyphen


def test_the_repeat_threshold_is_the_one_default_that_moved():
    """0.6 to 0.5, deliberately, and the floor beside it.

    Everything else here is the value the rules used to hard-code. This one is
    not: at 0.6 a five page report needs its header on three pages, and the case
    that prompted the change had one on two. See the test below for the shape of
    that document.
    """
    assert DEFAULTS.repeat_share == 0.5
    assert DEFAULTS.min_repeats == 2


def test_a_short_page_still_has_no_middle():
    # The rule that stopped body text on a four line page reading as a header.
    assert DEFAULTS.min_page_lines == DEFAULTS.edge * 2 + 2


# ── each setting reaches the rule it belongs to ──────────────────────────────

def test_the_hyphen_list_is_what_dehyphenate_consults():
    loose = Settings(keep_hyphen=frozenset({"part"}))
    assert paragraphs.dehyphenate("part-", "signed today", loose) == ("part-signed", "today")
    # And replaces the list rather than adding to it, so a prefix that was in the
    # defaults is gone unless the file names it again.
    assert paragraphs.dehyphenate("well-", "being matters", loose) == ("wellbeing", "matters")


def test_keeping_every_hyphen_is_a_single_flag():
    every = Settings(keep_all_hyphens=True)
    assert paragraphs.dehyphenate("settle-", "ment had moved", every) == ("settle-ment", "had moved")


def test_the_short_line_length_is_what_ends_a_paragraph():
    line = "held the line."
    assert paragraphs.is_break(line, "next", Settings(short_line=60)) is True
    assert paragraphs.is_break(line, "next", Settings(short_line=4)) is False


def test_the_heading_word_limit_is_what_separates_a_heading_from_a_list_item():
    title = "3.1.4 What counts as working alone"
    assert blocks.heading_level(title, None, Settings(max_heading_words=12)) is not None
    assert blocks.heading_level(title, None, Settings(max_heading_words=4)) is None


def test_the_note_depth_is_what_makes_a_line_a_footnote():
    line = "1 The 2019 report describes the method."
    assert notes.looks_like_note(line, 8, Settings(note_depth=10))
    assert not notes.looks_like_note(line, 8, Settings(note_depth=6))


def test_normalising_characters_can_be_turned_off():
    # A corpus archived for fidelity wants the ligature left alone.
    assert text.normalise("the ﬁling", Settings(normalise_characters=False)) == "the ﬁling"


def test_the_page_threshold_is_what_makes_furniture_possible():
    from tests.test_rules import page

    raw = "\f".join(page(top="Note", body=b) for b in ("body", "more"))
    kept = lambda s: "\n".join(l.text for l in furniture.strip(read(raw), s).lines)
    assert "Note" in kept(Settings(min_pages=3))
    assert "Note" not in kept(Settings(min_pages=2))


# ── reading the file ─────────────────────────────────────────────────────────

def test_a_section_sets_the_default_for_every_document(tmp_path):
    config = load(write(tmp_path, "[blocks]\nmax_heading_words = 4\n"))
    assert config.defaults.max_heading_words == 4
    assert config.for_document("anything.txt").max_heading_words == 4


def test_a_document_block_overrides_that_document_only(tmp_path):
    config = load(write(tmp_path, """
[blocks]
max_heading_words = 4

[documents."handbook.txt".blocks]
max_heading_words = 20
"""))
    assert config.for_document("handbook.txt").max_heading_words == 20
    assert config.for_document("report.txt").max_heading_words == 4


def test_a_document_block_leaves_the_other_settings_alone(tmp_path):
    config = load(write(tmp_path, """
[paragraphs]
short_line = 40

[documents."memo.txt".text]
normalise = false
"""))
    memo = config.for_document("memo.txt")
    assert memo.normalise_characters is False
    assert memo.short_line == 40


def test_no_config_file_means_the_defaults(tmp_path):
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    config = find(tmp_path / "a.txt")
    assert config.path is None
    assert config.for_document("a.txt") == DEFAULTS


def test_the_config_is_found_beside_the_document(tmp_path):
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    write(tmp_path, "[blocks]\nmax_heading_words = 7\n")
    assert find(tmp_path / "a.txt").defaults.max_heading_words == 7


def test_a_config_further_up_the_tree_is_not_found(tmp_path):
    # Deliberate. A file three directories away changing the output of a
    # conversion is hard to notice and harder to explain.
    write(tmp_path, "[blocks]\nmax_heading_words = 7\n")
    nested = tmp_path / "deeper"
    nested.mkdir()
    (nested / "a.txt").write_text("hello\n", encoding="utf-8")
    assert find(nested / "a.txt").defaults == DEFAULTS


# ── a config file that is wrong says so ──────────────────────────────────────

def test_an_unknown_key_is_an_error_and_names_the_alternatives(tmp_path):
    with pytest.raises(ConfigError) as caught:
        load(write(tmp_path, "[blocks]\nmax_headings_words = 4\n"))
    assert "max_headings_words" in str(caught.value)
    assert "max_heading_words" in str(caught.value)


def test_an_unknown_section_is_an_error(tmp_path):
    with pytest.raises(ConfigError):
        load(write(tmp_path, "[blocs]\nmax_heading_words = 4\n"))


@pytest.mark.parametrize("body", [
    '[blocks]\nmax_heading_words = "lots"\n',
    "[blocks]\nmax_heading_words = -1\n",
    "[furniture]\nrepeat_share = 2.5\n",
    "[furniture]\nrepeat_share = 0\n",
    "[text]\nnormalise = 1\n",
    "[paragraphs]\nkeep_hyphen = 3\n",
])
def test_a_value_of_the_wrong_shape_is_an_error(tmp_path, body):
    with pytest.raises(ConfigError):
        load(write(tmp_path, body))


def test_a_typo_in_a_document_nobody_converted_is_still_an_error(tmp_path):
    # Validated at load rather than at conversion, so the error arrives on the
    # run that introduced it rather than weeks later.
    with pytest.raises(ConfigError):
        load(write(tmp_path, '[documents."never.txt".blocks]\nnope = 1\n'))


def test_a_file_that_is_not_toml_is_an_error(tmp_path):
    with pytest.raises(ConfigError):
        load(write(tmp_path, "[furniture\n"))


# ── settings reach a whole conversion ────────────────────────────────────────

def test_settings_change_the_document_and_leave_nothing_behind():
    # Padded, because on a three line page the heading sits at the foot and the
    # footnote rule claims it before the heading rule ever runs.
    raw = (
        "1. A heading that runs to rather more words than usual here\n\nBody.\n"
        + "\n".join(f"filler {i}" for i in range(12))
        + "\n"
    )
    # The title is eleven words, so the default of twelve admits it.
    before = convert(raw).headings
    assert before == 1
    assert convert(raw, Settings(max_heading_words=4)).headings == 0
    assert convert(raw, Settings(max_heading_words=20)).headings == 1
    # No module state, so a conversion with settings leaves the next one alone.
    assert convert(raw).headings == before
