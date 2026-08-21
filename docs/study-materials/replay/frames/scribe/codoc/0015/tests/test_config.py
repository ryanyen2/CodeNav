"""Settings and scribe.toml.

The settings exist so one awkward document can be converted differently without
editing the source, so most of what is worth testing here is the reading of the
file and what happens when it says something scribe cannot act on.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scribe import config, furniture, paragraphs
from scribe.convert import convert
from scribe.lines import read
from scribe.report import render
from scribe.settings import DEFAULTS, Settings

FIXTURES = Path(__file__).parent.parent / "fixtures"


def write(folder: Path, text: str) -> Path:
    path = folder / config.CONFIG_NAME
    path.write_text(text, encoding="utf-8")
    return path


# ── the defaults ─────────────────────────────────────────────────────────────

def test_the_defaults_are_the_old_constants():
    assert DEFAULTS.repeat_share == 0.6
    assert DEFAULTS.edge == 2
    assert "well" in DEFAULTS.keep_hyphen


def test_merging_nothing_changes_nothing():
    assert DEFAULTS.merged() is DEFAULTS


def test_merging_ignores_values_that_are_not_there():
    assert DEFAULTS.merged(repeat_share=None).repeat_share == 0.6


# ── finding the file ─────────────────────────────────────────────────────────

def test_a_config_file_is_found_from_a_folder_below_it(tmp_path):
    write(tmp_path, "[defaults]\nrepeat_share = 0.4\n")
    deep = tmp_path / "one" / "two"
    deep.mkdir(parents=True)
    assert config.find(deep) is not None


def test_no_config_file_anywhere_is_not_an_error(tmp_path):
    conf = config.load(config.find(tmp_path))
    assert conf.for_document("anything.txt") == DEFAULTS


# ── reading it ───────────────────────────────────────────────────────────────

def test_defaults_apply_to_every_document(tmp_path):
    conf = config.load(write(tmp_path, "[defaults]\nedge = 3\n"))
    assert conf.for_document("report.txt").edge == 3
    assert conf.for_document("memo.txt").edge == 3


def test_a_document_section_wins_over_the_defaults(tmp_path):
    conf = config.load(write(
        tmp_path,
        '[defaults]\nrepeat_share = 0.8\n\n[document."survey.txt"]\nrepeat_share = 0.4\n',
    ))
    assert conf.for_document("survey.txt").repeat_share == 0.4
    assert conf.for_document("report.txt").repeat_share == 0.8


def test_a_setting_scribe_does_not_have_is_refused(tmp_path):
    path = write(tmp_path, "[defaults]\nrepeat_shore = 0.4\n")
    with pytest.raises(config.ConfigError) as raised:
        config.load(path)
    assert "repeat_shore" in str(raised.value)


def test_a_share_outside_the_range_is_refused(tmp_path):
    path = write(tmp_path, "[defaults]\nrepeat_share = 1.4\n")
    with pytest.raises(config.ConfigError):
        config.load(path)


def test_an_edge_below_one_is_refused(tmp_path):
    path = write(tmp_path, "[defaults]\nedge = 0\n")
    with pytest.raises(config.ConfigError):
        config.load(path)


def test_the_document_it_names_is_in_the_message(tmp_path):
    path = write(tmp_path, '[document."survey.txt"]\nrepeat_share = 3\n')
    with pytest.raises(config.ConfigError) as raised:
        config.load(path)
    assert "survey.txt" in str(raised.value)


# ── the rules read them ──────────────────────────────────────────────────────

def five_pages(header_on: int) -> str:
    """Five pages, the same header on the first `header_on` of them."""
    pages = []
    for number in range(1, 6):
        top = "Appendix A" if number <= header_on else f"Section {number}"
        pages.append(
            f"{top}\nbody {number}\nmore body {number}\n"
            f"still more {number}\nlast {number}\nfoot"
        )
    return "\f".join(pages)


def test_a_header_on_two_pages_of_five_survives_the_default():
    assert "appendix a" not in furniture.find_repeated(read(five_pages(2)))


def test_a_lower_share_removes_a_header_that_repeats_less_often():
    found = furniture.find_repeated(read(five_pages(2)), DEFAULTS.merged(repeat_share=0.4))
    assert "appendix a" in found


def test_an_empty_prefix_list_drops_every_hyphen():
    bare = DEFAULTS.merged(keep_hyphen=())
    assert paragraphs.dehyphenate("well-", "being follows", bare)[0].endswith("wellbeing")


def test_the_prefixes_keep_their_hyphen_by_default():
    assert paragraphs.dehyphenate("well-", "being follows")[0].endswith("well-being")


def test_converting_with_the_defaults_matches_converting_with_nothing():
    raw = (FIXTURES / "report.txt").read_text(encoding="utf-8")
    assert convert(raw).markdown == convert(raw, DEFAULTS).markdown


# ── the report ───────────────────────────────────────────────────────────────

def test_the_report_names_the_document_and_the_settings_used():
    raw = (FIXTURES / "report.txt").read_text(encoding="utf-8")
    settings = Settings(repeat_share=0.5)
    text = render("report.txt", convert(raw, settings), settings)
    assert "# report.txt" in text
    assert "repeat_share: 0.5" in text
    assert "50% of the pages" in text
