"""The config file, and the settings the rules are handed.

Two things are being tested here. The first is that a config file says what it
looks like it says, and complains clearly when it does not. The second, and the
one worth keeping, is that the defaults still convert a document exactly the way
the module constants used to: a config file nobody writes must change nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scribe import blocks, config, furniture, notes, paragraphs, text
from scribe.config import DEFAULTS, Settings
from scribe.convert import convert
from scribe.lines import read


# ── the defaults are the old constants ───────────────────────────────────────

def test_the_defaults_are_the_values_the_constants_had():
    """If one of these changes, every document converted so far converts differently.

    They are written out rather than derived so that a change to one is a change
    to this test, and has to be argued for in the diff.

    `repeat_share` is the one that has moved: it was 0.6, which on a five page
    report puts the threshold at three pages and leaves a header that appears on
    two of them in the prose. 0.4 is two pages in five exactly, and it holds for
    six and seven page documents too, where 0.5 would not. The cost is on long
    documents, where a line near an edge on 16 pages of 40 is now furniture where
    it used to take 24. `repeat_share = 0.6` in a config file restores it.
    """
    assert DEFAULTS.furniture.edge == 2
    assert DEFAULTS.furniture.repeat_share == 0.4
    assert DEFAULTS.furniture.min_repeats == 2
    assert DEFAULTS.furniture.min_pages == 3
    assert DEFAULTS.furniture.min_page_lines == 6
    assert DEFAULTS.paragraphs.short_line == 60
    assert DEFAULTS.paragraphs.keep_all_hyphens is False
    assert "well" in DEFAULTS.paragraphs.keep_hyphen
    assert DEFAULTS.blocks.max_heading_words == 12
    assert DEFAULTS.notes.foot_zone == 6
    assert DEFAULTS.notes.collect is True
    assert DEFAULTS.text.normalise is True


def test_passing_the_defaults_is_the_same_as_passing_nothing():
    source = "1. Summary\n\nA line that was bro-\nken across two lines.\n"
    assert convert(source).markdown == convert(source, Settings()).markdown


# ── reading the file ─────────────────────────────────────────────────────────

def test_a_top_level_section_changes_every_document():
    conf = config.parse("[blocks]\nmax_heading_words = 3")
    assert conf.for_document("anything.txt").blocks.max_heading_words == 3


def test_a_document_section_changes_only_that_document():
    conf = config.parse('[document."memo.txt".text]\nnormalise = false')
    assert conf.for_document("memo.txt").text.normalise is False
    assert conf.for_document("report.txt").text.normalise is True


def test_a_document_pattern_can_be_a_glob():
    conf = config.parse('[document."*.txt".notes]\ncollect = false')
    assert conf.for_document("report.txt").notes.collect is False


def test_the_last_matching_section_wins():
    # Order in the file is the order they apply, so a corpus can set a rule for
    # everything and then take it back for the one document that needs it.
    conf = config.parse(
        '[document."*.txt".blocks]\nmax_heading_words = 4\n'
        '[document."memo.txt".blocks]\nmax_heading_words = 9\n'
    )
    assert conf.for_document("memo.txt").blocks.max_heading_words == 9
    assert conf.for_document("other.txt").blocks.max_heading_words == 4


def test_the_settings_remember_where_they_came_from():
    # The conversion report says which config was in force, and it can only say
    # so if the settings carry it.
    conf = config.parse('[document."memo.txt".text]\nnormalise = false')
    assert conf.for_document("memo.txt").applied == ("memo.txt",)
    assert conf.for_document("report.txt").applied == ()


# ── complaining clearly ──────────────────────────────────────────────────────

def test_an_unknown_setting_is_an_error_naming_it():
    # Silently ignoring it is the failure mode that wastes an afternoon: the file
    # looks right, the run looks right, and nothing changed.
    with pytest.raises(config.ConfigError, match="repeat_shore"):
        config.parse("[furniture]\nrepeat_shore = 0.5")


def test_an_unknown_section_is_an_error():
    with pytest.raises(config.ConfigError, match="headings"):
        config.parse("[headings]\nmax_words = 4")


def test_the_wrong_type_is_an_error():
    with pytest.raises(config.ConfigError, match="whole number"):
        config.parse("[furniture]\nedge = true")


def test_a_number_is_not_accepted_where_a_flag_belongs():
    # bool is a subclass of int in Python, so this needs saying out loud.
    with pytest.raises(config.ConfigError, match="true or false"):
        config.parse("[text]\nnormalise = 1")


def test_a_typo_in_a_document_nobody_converts_is_still_an_error():
    # Validated when the file is read rather than when a document happens to
    # match, so the mistake surfaces on the first run and not on some later one.
    with pytest.raises(config.ConfigError, match="unknown setting"):
        config.parse('[document."never.txt".notes]\nfoot_zne = 3')


def test_broken_toml_names_the_file():
    with pytest.raises(config.ConfigError, match="house.toml"):
        config.parse("[furniture", path=Path("house.toml"))


# ── finding the file ─────────────────────────────────────────────────────────

def test_the_config_is_found_from_a_subdirectory(tmp_path):
    # One config at the root of a corpus covers the documents filed beneath it.
    (tmp_path / "scribe.toml").write_text("[blocks]\nmax_heading_words = 5\n")
    deep = tmp_path / "2026" / "surveys"
    deep.mkdir(parents=True)
    document = deep / "report.txt"
    document.write_text("x")
    assert config.discover(document).for_document("report.txt").blocks.max_heading_words == 5


def test_no_config_anywhere_is_not_an_error(tmp_path):
    conf = config.discover(tmp_path)
    assert conf.path is None
    assert conf.for_document("report.txt") == Settings()


# ── the settings reach the rules ─────────────────────────────────────────────

def test_a_longer_heading_is_allowed_when_the_setting_says_so():
    line = "1. Entering water above the knee, whether or not you are wearing a buoyancy aid"
    assert blocks.heading_level(line) is None
    generous = config.parse("[blocks]\nmax_heading_words = 20").for_document("x.txt")
    assert blocks.heading_level(line, None, generous.blocks) is not None


def test_keeping_every_hyphen_is_one_setting():
    settings = config.parse("[paragraphs]\nkeep_all_hyphens = true").for_document("x.txt")
    assert paragraphs.dehyphenate("settle-", "ment had moved") == ("settlement", "had moved")
    assert paragraphs.dehyphenate("settle-", "ment had moved", settings.paragraphs) == (
        "settle-ment", "had moved")


def test_the_footnote_zone_can_be_narrowed():
    settings = config.parse("[notes]\nfoot_zone = 1").for_document("x.txt")
    assert notes.looks_like_note("1 The 2019 report.", from_bottom=4)
    assert not notes.looks_like_note("1 The 2019 report.", 4, settings.notes)


def test_normalisation_can_be_turned_off_for_an_archive():
    settings = config.parse("[text]\nnormalise = false").for_document("x.txt")
    assert text.normalise("the ﬁling") == "the filing"
    assert text.normalise("the ﬁling", settings.text) == "the ﬁling"


def test_a_corpus_can_add_a_substitution_of_its_own():
    settings = config.parse('[text.extra]\n"™" = "(tm)"').for_document("x.txt")
    assert text.normalise("Fastigo™", settings.text) == "Fastigo(tm)"


BODY = [
    "survey notes", "the weather held", "readings were taken", "access was clear",
    "the tide was out", "equipment worked", "the light was poor", "we returned",
    "nothing to report", "the road was open", "samples were bagged", "all quiet",
]
PLACES = ["north", "south", "east", "west", "middle", "upper", "lower"]


def pages(*, header_at: int = 0, count: int = 3, header_on: set[int] | None = None) -> str:
    """`count` pages carrying the same header, by default on every one of them.

    The body lines are worded rather than numbered because the repeat rule folds
    digits before comparing, so a body of "line 1", "line 2" is one repeated line
    as far as it is concerned, and the test would be about the fixture.
    """
    out = []
    for page in range(count):
        lines = [f"{PLACES[page]}: {body}" for body in BODY]
        if header_on is None or page in header_on:
            lines[header_at] = "Handbook"
        out.append("\n".join(lines) + "\n")
    return "\f".join(out)


def surviving(raw: str, settings=None) -> str:
    doc = read(raw)
    kept = furniture.strip(doc, settings) if settings else furniture.strip(doc)
    return "\n".join(line.text for line in kept.lines)


# ── the repeat threshold ─────────────────────────────────────────────────────

def test_a_header_on_two_pages_in_five_is_furniture():
    """The case the default was chosen for.

    A five page report whose running header starts after the title page, or stops
    before the appendices, appears on two pages. Under the old 0.6 the threshold
    was three and the header stayed in the prose.
    """
    assert "Handbook" not in surviving(pages(count=5, header_on={2, 3}))


def test_a_header_on_two_pages_in_six_and_seven_is_furniture_too():
    # 0.5 would have caught the five page case and missed these, which is why it
    # was not the default chosen.
    assert "Handbook" not in surviving(pages(count=6, header_on={1, 4}))
    assert "Handbook" not in surviving(pages(count=7, header_on={1, 5}))


def test_a_line_on_two_pages_of_forty_is_still_a_coincidence():
    # The share is what keeps this from being furniture. It is the whole reason
    # the threshold is a share rather than a count.
    assert "Handbook" in surviving(pages(count=40, header_on={11, 12}))


def test_the_floor_is_a_setting_now():
    # A corpus that wants more evidence before dropping a line can ask for it.
    raw = pages(count=5, header_on={2, 3})
    assert "Handbook" not in surviving(raw)
    strict = config.parse("[furniture]\nmin_repeats = 4").for_document("x.txt")
    assert "Handbook" in surviving(raw, strict.furniture)


def test_the_floor_carries_documents_too_short_for_the_share_to_mean_anything():
    # Three pages at a share of 0.4 works out at one page, which would make a
    # line that appears once furniture. The floor is what stops that.
    raw = pages(count=3, header_on={0})
    assert "Handbook" in surviving(raw)


def test_a_threshold_below_two_is_refused():
    # It would empty a document rather than tidy it: every line near an edge
    # would clear a threshold of one.
    with pytest.raises(config.ConfigError, match="at least 2"):
        config.parse("[furniture]\nmin_repeats = 1")


def test_a_share_outside_nought_to_one_is_refused():
    with pytest.raises(config.ConfigError, match="between 0 and 1"):
        config.parse("[furniture]\nrepeat_share = 60")


def test_a_refused_value_names_the_file_and_the_section():
    with pytest.raises(config.ConfigError, match=r"scribe.toml \[furniture\]"):
        config.parse("[furniture]\nmin_repeats = 0", path=Path("scribe.toml"))


def test_a_wider_edge_catches_a_header_that_sits_further_in():
    # The default only looks two lines into a page. A document whose header sits
    # under a masthead needs a wider margin, and this is the setting for it.
    raw = pages(header_at=3)
    assert "Handbook" in "\n".join(line.text for line in furniture.strip(read(raw)).lines)

    wide = config.parse("[furniture]\nedge = 4").for_document("x.txt")
    kept = furniture.strip(read(raw), wide.furniture)
    assert "Handbook" not in "\n".join(line.text for line in kept.lines)


def test_furniture_detection_can_be_asked_to_wait_for_more_pages():
    raw = pages(header_at=0)
    assert "Handbook" not in "\n".join(line.text for line in furniture.strip(read(raw)).lines)

    patient = config.parse("[furniture]\nmin_pages = 10").for_document("x.txt")
    kept = furniture.strip(read(raw), patient.furniture)
    assert "Handbook" in "\n".join(line.text for line in kept.lines)


# ── end to end ───────────────────────────────────────────────────────────────

def test_two_documents_in_one_run_can_be_converted_two_ways():
    """The whole point of the config file.

    The same code, the same run, two documents, two sets of rules. Before this
    the only way to get here was to edit the source between runs.
    """
    conf = config.parse('[document."memo.txt".text]\nnormalise = false')
    source = "the ﬁling backlog\n"
    memo = convert(source, conf.for_document("memo.txt"))
    other = convert(source, conf.for_document("report.txt"))
    assert "ﬁ" in memo.markdown
    assert "filing" in other.markdown


def test_notes_left_in_place_when_they_are_not_collected():
    raw = "A sentence here.\n\n1 The 2019 report describes the method.\n"
    conf = config.parse("[notes]\ncollect = false")
    out = convert(raw, conf.for_document("x.txt")).markdown
    assert "[^1]" not in out
    assert "1 The 2019 report describes the method." in out
