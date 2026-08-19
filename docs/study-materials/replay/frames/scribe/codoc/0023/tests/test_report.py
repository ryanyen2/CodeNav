"""The note written beside each converted document.

The report exists to show the lossy decisions, so the tests are mostly about
whether it names them. The first one is about something else: a document called
report.txt converts to report.md, and a conversion report called report.md would
overwrite it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scribe import config, report
from scribe.cli import main
from scribe.convert import convert

FIXTURES = Path(__file__).parent.parent / "fixtures"


def converted(name: str = "report"):
    raw = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
    return convert(raw), FIXTURES / f"{name}.txt", FIXTURES / f"{name}.md"


def rendered(name: str = "report") -> str:
    result, source, markdown = converted(name)
    return report.render(result, source, markdown)


# ── the name ─────────────────────────────────────────────────────────────────

def test_the_report_does_not_overwrite_the_document_it_reports_on():
    """report.txt converts to report.md. The report cannot also be report.md.

    This is the reason for the `<name>.report.md` shape, and it is the one thing
    here that would lose somebody's output rather than merely annoy them.
    """
    markdown = Path("fixtures/report.md")
    assert report.name_for(markdown) != markdown


def test_two_documents_do_not_share_a_report():
    assert report.name_for(Path("survey.2026.md")) != report.name_for(Path("survey.md"))


# ── what it says ─────────────────────────────────────────────────────────────

def test_it_names_the_lines_it_removed():
    # The furniture rule is the one that can eat a real heading, so the report
    # lists what went rather than only counting it.
    assert "Marine Institute" in rendered()


def test_it_counts_a_header_that_repeated():
    assert "3 times" in rendered()


def test_it_lists_the_words_it_rejoined():
    out = rendered()
    assert "photogrammetric" in out
    assert "settlement" in out


def test_it_lists_the_footnotes_it_moved():
    assert "[^1]" in rendered()


def test_it_says_which_settings_were_in_force():
    result, source, markdown = converted("memo")
    conf = config.parse('[document."memo.txt".text]\nnormalise = false', path=Path("scribe.toml"))
    result.settings = conf.for_document("memo.txt")
    out = report.render(result, source, markdown)
    assert "scribe.toml" in out
    assert "text.normalise" in out


def test_it_says_when_there_was_no_config_at_all():
    assert "No `scribe.toml` was found" in rendered()


def test_a_document_with_no_furniture_has_no_furniture_section():
    # An empty heading with nothing under it is worse than no heading.
    assert "removed as page furniture" not in rendered("memo")


def test_the_report_is_short():
    # It is read beside the document, not instead of it.
    assert len(rendered().splitlines()) < 40


def test_the_same_conversion_reports_the_same_thing():
    # No timestamp, so the report is diffable: a change in it is a change in the
    # conversion, not in the clock.
    assert rendered() == rendered()


# ── through the command ──────────────────────────────────────────────────────

@pytest.fixture
def corpus(tmp_path):
    for name in ("report", "memo", "handbook"):
        (tmp_path / f"{name}.txt").write_text(
            (FIXTURES / f"{name}.txt").read_text(encoding="utf-8"), encoding="utf-8"
        )
    return tmp_path


def test_converting_a_file_writes_the_report_beside_it(corpus):
    assert main(["convert", str(corpus / "report.txt")]) == 0
    assert (corpus / "report.md").is_file()
    assert (corpus / "report.report.md").is_file()


def test_the_markdown_is_the_conversion_not_the_report(corpus):
    main(["convert", str(corpus / "report.txt")])
    assert "## Summary" in (corpus / "report.md").read_text()


def test_writing_to_stdout_writes_no_report(corpus, capsys):
    # stdout is for piping the Markdown somewhere. A file appearing beside the
    # source would be a surprise.
    main(["convert", str(corpus / "memo.txt"), "-"])
    assert not list(corpus.glob("*.report.md"))
    assert "backlog" in capsys.readouterr().out


def test_nothing_but_the_markdown_goes_to_stdout(corpus, capsys):
    """`scribe convert memo.txt - > memo.md` must write a document, not a document
    with a line of statistics stapled to the end of it.

    The summary is still printed, on stderr, so a person watching still sees it.
    """
    main(["convert", str(corpus / "memo.txt"), "-"])
    captured = capsys.readouterr()
    assert "7 paragraphs" not in captured.out
    assert "7 paragraphs" in captured.err
    assert captured.out.endswith("clerk back.\n")


def test_the_report_can_be_turned_off(corpus):
    main(["convert", str(corpus / "memo.txt"), "--no-report"])
    assert (corpus / "memo.md").is_file()
    assert not (corpus / "memo.report.md").exists()


def test_the_config_can_turn_the_report_off(corpus):
    (corpus / "scribe.toml").write_text("[report]\nwrite = false\n")
    main(["convert", str(corpus / "memo.txt")])
    assert not (corpus / "memo.report.md").exists()


def test_check_writes_nothing(corpus):
    assert main(["check", str(corpus)]) == 0
    assert not list(corpus.glob("*.md"))


def test_a_broken_config_stops_the_run(corpus, capsys):
    (corpus / "scribe.toml").write_text("[furniture]\nrepeat_shore = 0.5\n")
    assert main(["convert", str(corpus / "memo.txt")]) == 2
    assert "repeat_shore" in capsys.readouterr().err
    assert not (corpus / "memo.md").exists()


def test_a_named_config_is_used(corpus):
    elsewhere = corpus / "other.toml"
    elsewhere.write_text('[document."memo.txt".text]\nnormalise = false\n')
    main(["convert", str(corpus / "memo.txt"), "--config", str(elsewhere)])
    assert "ﬁling" in (corpus / "memo.md").read_text(encoding="utf-8")
    assert "other.toml" in (corpus / "memo.report.md").read_text(encoding="utf-8")
