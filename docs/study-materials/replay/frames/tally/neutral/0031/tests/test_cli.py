"""The command line.

Mostly about the options, because that is where this can go wrong quietly: a
flag that is silently read as a filename, or a rules file that fails to load and
leaves the run using something else.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tally.cli import main

FIXTURES = Path(__file__).parent.parent / "fixtures"
STATEMENT = str(FIXTURES / "current.csv")


def test_a_summary_goes_to_stdout_when_asked(capsys):
    assert main(["summarise", STATEMENT, "-"]) == 0
    assert "## 2026-01" in capsys.readouterr().out


def test_by_week_changes_the_grouping(capsys):
    assert main(["summarise", STATEMENT, "-", "--by-week"]) == 0
    out = capsys.readouterr().out
    assert "## 2026-W01" in out and "## 2026-01" not in out


def test_the_option_can_go_before_the_file(capsys):
    # It is removed from the arguments wherever it appears, so the file is still
    # found and is not mistaken for the flag.
    assert main(["summarise", "--by-week", STATEMENT, "-"]) == 0
    assert "## 2026-W01" in capsys.readouterr().out


def test_writing_goes_beside_the_statement(tmp_path, capsys):
    statement = tmp_path / "mine.csv"
    statement.write_text((FIXTURES / "current.csv").read_text(), encoding="utf-8")
    assert main(["summarise", str(statement)]) == 0
    assert (tmp_path / "mine.md").exists()
    assert "mine.csv:" in capsys.readouterr().out


def test_check_writes_nothing(tmp_path, capsys):
    statement = tmp_path / "mine.csv"
    statement.write_text((FIXTURES / "current.csv").read_text(), encoding="utf-8")
    assert main(["check", str(tmp_path)]) == 0
    assert not (tmp_path / "mine.md").exists()
    assert "checked 1 statements" in capsys.readouterr().out


def test_a_different_rules_file_can_be_pointed_at(tmp_path, capsys):
    # The reason the rules are in a file at all.
    mine = tmp_path / "mine.toml"
    mine.write_text(
        "[recurring]\nmonths = 3\n[money]\npositive_share = 0.8\n"
        "[transfers]\nwords = [\"transfer\"]\nhandling = \"show\"\n"
        "[[rule]]\npattern = 'tesco'\ncategory = \"the weekly shop\"\n",
        encoding="utf-8",
    )
    assert main(["summarise", STATEMENT, "-", "--rules", str(mine)]) == 0
    assert "the weekly shop" in capsys.readouterr().out


def test_transfers_can_be_hidden_again_from_the_rules_file(tmp_path, capsys):
    mine = tmp_path / "quiet.toml"
    mine.write_text(
        "[recurring]\nmonths = 3\n[money]\npositive_share = 0.8\n"
        "[transfers]\nwords = [\"transfer\"]\nhandling = \"hide\"\n"
        "[[rule]]\npattern = 'tesco'\ncategory = \"groceries\"\n",
        encoding="utf-8",
    )
    assert main(["summarise", STATEMENT, "-", "--rules", str(mine)]) == 0
    assert "moved" not in capsys.readouterr().out


@pytest.mark.parametrize("argv", [
    ["summarise", STATEMENT, "-", "--rules"],                 # no path given
    ["summarise", STATEMENT, "-", "--rules", "nope.toml"],    # no such file
    ["summarise", STATEMENT, "-", "--by-month"],              # not an option
    ["summarise", "nope.csv"],
    ["summarise"],
    ["nonsense"],
])
def test_the_ways_of_getting_it_wrong_all_report_and_stop(argv, capsys):
    assert main(argv) == 2
    assert capsys.readouterr().err.strip()


def test_a_broken_rules_file_stops_before_anything_is_summarised(tmp_path, capsys):
    broken = tmp_path / "broken.toml"
    broken.write_text("[[rule]]\npattern = '('\ncategory = \"x\"\n", encoding="utf-8")
    assert main(["summarise", STATEMENT, "-", "--rules", str(broken)]) == 2
    captured = capsys.readouterr()
    assert "broken.toml" in captured.err
    assert not captured.out


def test_help_needs_no_arguments(capsys):
    assert main([]) == 0
    assert "--by-week" in capsys.readouterr().out
