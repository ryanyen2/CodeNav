"""The conversion report, and the files the command leaves behind.

The report exists because the lossy steps are invisible in the Markdown: a
running header that was removed leaves no trace, and the only way to see it went
is to be told. So the tests here are mostly about the removals being named.

The file names matter more than they look. `convert` writes two files now, and
one of the sample documents is called `report.txt`, so a report file named
`report.md` would overwrite the Markdown of the very document it describes.
"""
from __future__ import annotations

from pathlib import Path

from scribe import report
from scribe.cli import main
from scribe.convert import convert
from scribe.settings import Config, Settings, load

FIXTURES = Path(__file__).parent.parent / "fixtures"


def rendered(name: str, settings: Settings | None = None, config: Config | None = None) -> str:
    source = FIXTURES / f"{name}.txt"
    result = convert(source.read_text(encoding="utf-8"), settings or Settings())
    return report.render(result, source, source.with_suffix(".md"), config)


# ── what it says ─────────────────────────────────────────────────────────────

def test_it_names_the_document_and_what_was_written():
    out = rendered("report")
    assert out.startswith("# report.txt")
    assert "`report.md`" in out


def test_it_carries_the_same_counts_as_the_command_prints():
    result = convert((FIXTURES / "report.txt").read_text(encoding="utf-8"))
    assert result.summary() in rendered("report")


def test_it_names_the_furniture_that_was_removed():
    # The whole point. The running header is gone from the Markdown without a
    # trace, so the report is the only place it survives.
    out = rendered("report")
    assert "Marine Institute" in out
    assert "(3 times)" in out


def test_it_says_the_footnotes_moved():
    assert "collected at the end" in rendered("report")


def test_a_document_with_nothing_removed_says_nothing_about_removals():
    # The memo has no furniture and no notes, and a report full of empty
    # headings would be worse than a short one.
    out = rendered("memo")
    assert "## Removed" not in out
    assert "## Moved" not in out


# ── what it says about settings ──────────────────────────────────────────────

def test_settings_at_their_defaults_are_one_line_not_a_table():
    assert "Every setting is at its default." in rendered("memo")


def test_a_changed_setting_is_named_with_the_default_beside_it():
    out = rendered("memo", Settings(max_heading_words=4))
    assert "`max_heading_words`: 4 (default 12)" in out


def test_a_flag_is_spelled_the_way_the_config_file_spells_it():
    # So that a line can be copied out of the report and into scribe.toml.
    out = rendered("memo", Settings(normalise_characters=False))
    assert "false (default true)" in out


def test_it_says_which_config_file_the_settings_came_from():
    config = load(FIXTURES / "scribe.toml")
    assert "scribe.toml" in rendered("memo", config.for_document("memo.txt"), config)


def test_it_says_so_when_there_was_no_config_file():
    assert "built-in defaults" in rendered("memo")


# ── the report is a function of its input ────────────────────────────────────

def test_rendering_twice_gives_the_same_thing():
    # No timestamp, deliberately: a report checked into git should only change
    # when the conversion changes.
    assert rendered("report") == rendered("report")


# ── the files the command writes ─────────────────────────────────────────────

def copy_fixture(name: str, into: Path) -> Path:
    target = into / f"{name}.txt"
    target.write_text((FIXTURES / f"{name}.txt").read_text(encoding="utf-8"), encoding="utf-8")
    return target


def test_convert_writes_the_markdown_and_a_report_beside_it(tmp_path):
    source = copy_fixture("memo", tmp_path)
    assert main(["convert", str(source)]) == 0
    assert (tmp_path / "memo.md").is_file()
    assert (tmp_path / "memo.report.md").is_file()


def test_the_report_does_not_overwrite_the_markdown_it_describes(tmp_path):
    # `report.txt` converts to `report.md`. A report file also called
    # `report.md` would clobber it, which is why the name is `report.report.md`.
    source = copy_fixture("report", tmp_path)
    assert main(["convert", str(source)]) == 0
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "## Summary" in markdown
    assert "# report.txt" not in markdown
    assert (tmp_path / "report.report.md").read_text(encoding="utf-8").startswith("# report.txt")


def test_no_report_writes_only_the_markdown(tmp_path):
    source = copy_fixture("memo", tmp_path)
    assert main(["convert", str(source), "--no-report"]) == 0
    assert (tmp_path / "memo.md").is_file()
    assert not (tmp_path / "memo.report.md").exists()


def test_writing_to_stdout_leaves_no_files_at_all(tmp_path, capsys):
    source = copy_fixture("memo", tmp_path)
    assert main(["convert", str(source), "-"]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["memo.txt"]
    # And what lands on stdout is the Markdown alone, so it can be piped.
    out = capsys.readouterr().out
    assert out.startswith("Note on the filing backlog")
    assert "pages," not in out


def test_check_still_writes_nothing(tmp_path):
    copy_fixture("memo", tmp_path)
    copy_fixture("report", tmp_path)
    assert main(["check", str(tmp_path)]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["memo.txt", "report.txt"]


def test_a_broken_config_stops_the_run_and_writes_nothing(tmp_path):
    source = copy_fixture("memo", tmp_path)
    (tmp_path / "scribe.toml").write_text("[blocks]\nnope = 1\n", encoding="utf-8")
    assert main(["convert", str(source)]) == 2
    assert not (tmp_path / "memo.md").exists()
