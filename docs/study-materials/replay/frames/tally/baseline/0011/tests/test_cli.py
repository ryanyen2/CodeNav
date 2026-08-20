"""The command, mostly about which file gets written.

A weekly summary must not land on top of a monthly one. Somebody who runs both
over a statement they care about should end up with both.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tally.cli import main

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def statement(tmp_path):
    # boundary.csv, because every merchant in it has a rule: the rules as shipped
    # stop on a merchant they do not know, which is its own test below.
    copy = tmp_path / "boundary.csv"
    shutil.copy(FIXTURES / "boundary.csv", copy)
    return copy


@pytest.fixture
def unknown_merchant(tmp_path):
    copy = tmp_path / "current.csv"
    shutil.copy(FIXTURES / "current.csv", copy)
    return copy


def test_a_summary_is_written_beside_the_statement(statement):
    assert main(["summarise", str(statement)]) == 0
    assert (statement.parent / "boundary.md").exists()


def test_by_week_writes_beside_the_monthly_one_rather_than_over_it(statement):
    main(["summarise", str(statement)])
    monthly = (statement.parent / "boundary.md").read_text(encoding="utf-8")

    assert main(["summarise", str(statement), "--by-week"]) == 0
    weekly = statement.parent / "boundary.weekly.md"
    assert weekly.exists(), "the weekly summary is its own file"
    assert (statement.parent / "boundary.md").read_text(encoding="utf-8") == monthly
    assert "-W" in weekly.read_text(encoding="utf-8")


def test_a_dash_still_prints_instead_of_writing(statement, capsys):
    assert main(["summarise", str(statement), "-", "--by-week"]) == 0
    assert "## 2026-W" in capsys.readouterr().out
    assert not (statement.parent / "boundary.weekly.md").exists()


def test_check_writes_nothing(tmp_path, statement):
    before = sorted(p.name for p in tmp_path.iterdir())
    assert main(["check", str(tmp_path), "--by-week"]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_a_merchant_with_no_rule_stops_the_run(unknown_merchant, capsys):
    # And writes nothing: a summary missing a merchant is the thing being
    # avoided, so half a summary on disk would be worse than none.
    assert main(["summarise", str(unknown_merchant)]) == 2
    assert "MOONLIGHT RECORDS" in capsys.readouterr().err
    assert not (unknown_merchant.parent / "current.md").exists()


def test_check_carries_on_past_a_statement_that_stopped(tmp_path, statement,
                                                        unknown_merchant, capsys):
    # The whole point of `check` is the folder in one go, so the one that stopped
    # is reported and the rest are still summarised. The exit code still says no.
    assert main(["check", str(tmp_path)]) == 2
    out = capsys.readouterr()
    assert "boundary.csv: 7 rows" in out.out
    assert "1 stopped" in out.out
    assert "MOONLIGHT RECORDS" in out.err


def test_a_missing_file_is_reported_not_traced(tmp_path, capsys):
    assert main(["summarise", str(tmp_path / "nothing.csv")]) == 2
    assert "no such file" in capsys.readouterr().err


def test_the_flag_on_its_own_is_not_a_command(capsys):
    assert main(["--by-week"]) == 2
    assert "needs a command" in capsys.readouterr().err


def test_an_option_that_does_not_exist_says_so(statement, capsys):
    # --by-month is the one somebody types after reading --by-week. Without this
    # it would be taken for a file name and come back as "no such file".
    assert main(["summarise", str(statement), "--by-month"]) == 2
    assert "unknown option: --by-month" in capsys.readouterr().err
