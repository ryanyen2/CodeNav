"""The three sample statements, end to end.

Each exists to exercise something the others cannot. The current account has
duplicates, transfers, refunds and recurring payments; the other bank names its
columns differently and exports spending as positive; the boundary file has
transactions made in one month and posted in the next.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from tally import settings
from tally.summary import summarise

FIXTURES = Path(__file__).parent.parent / "fixtures"
RULES = settings.load()


def run(name: str, by: str = "month"):
    return summarise((FIXTURES / f"{name}.csv").read_text(encoding="utf-8"), RULES, by=by)


# ── the current account ──────────────────────────────────────────────────────

def test_the_repeated_shop_is_counted_once():
    assert run("current").duplicates == 1


def test_both_legs_of_every_transfer_survive():
    # Four transfer rows: a same-day pair in January, then one a month. If the
    # duplicate rule saw the pair, this would be three.
    assert run("current").transfers == 4


def test_transfers_are_shown_but_left_out_of_the_spending():
    """The 300 a month into savings is visible, and no total claims it was spent.

    It used to be dropped after the transfer check and appear nowhere, so a
    standing order into savings was money that left the account and could not be
    found in the summary at all.
    """
    result = run("current")
    january = next(p for p in result.periods if p.name == "2026-01")

    assert january.moved == Decimal("-600.00")      # the same-day pair, both legs
    assert january.total == Decimal("1115.46")      # unchanged: still only spending
    assert "transfers" not in january.by_category

    out = result.text()
    assert "transfers           -600.00" in out
    assert "(not in the total)" in out


def test_the_standing_order_into_savings_is_a_fixed_commitment():
    # 300 on the same day of three months running. It is exactly the kind of
    # thing the recurring list is for, and it was missing from it.
    assert "transfer to savings" in run("current").recurring


def test_a_refund_nets_within_its_own_month_and_not_across_months():
    """Boots: 8.60 out in January, 8.60 back in February.

    Netting happens inside a month, so January still shows the spend and
    February shows the money back. That is a real limitation rather than a bug:
    a summary of what you spent in January should say what you spent in January,
    and a refund that arrived in February is a February event. Somebody who
    wanted the refund to reduce January would have to decide what to do when the
    refund arrives after the summary has been read.
    """
    result = run("current")
    january = next(p for p in result.periods if p.name == "2026-01")
    february = next(p for p in result.periods if p.name == "2026-02")
    assert january.by_category["health"] == Decimal("-8.60")
    assert february.by_category["health"] == Decimal("8.60")


def test_the_fixed_commitments_are_found():
    found = run("current").recurring
    assert any("netflix" in name for name in found)
    assert any("rent" in name for name in found)


def test_an_unknown_merchant_is_visible_rather_than_hidden():
    result = run("current")
    assert result.uncategorised == 1
    assert "uncategorised" in result.text()


# ── the other bank ───────────────────────────────────────────────────────────

def test_different_column_names_still_read():
    assert run("other-bank").rows_read == 13


def test_spending_exported_as_positive_is_flipped():
    # Every total should be money out, so negative.
    for month in run("other-bank").periods:
        assert month.total < 0


# ── the month boundary ───────────────────────────────────────────────────────

def test_a_transaction_made_on_the_31st_is_january():
    january = next(p for p in run("boundary").periods if p.name == "2026-01")
    # Aldi, Tesco and Shell: 33.40 + 78.90 + 60.00
    assert january.total == Decimal("-172.30")


def test_and_the_ones_made_in_february_are_february():
    # Pret 4.85, TfL 28.00, Lidl 45.15. The Lidl was made on the 28th and posted
    # in March, which is the same question from the other side.
    february = next(p for p in run("boundary").periods if p.name == "2026-02")
    assert february.total == Decimal("-78.00")


# ── by week ──────────────────────────────────────────────────────────────────

def test_the_same_statement_by_week():
    result = run("current", by="week")
    assert result.by == "week"
    assert all(p.name.count("-W") == 1 for p in result.periods)
    assert "weeks" in result.line()


def test_grouping_by_week_moves_the_money_around_but_does_not_change_it():
    """The same statement, cut two ways, still adds up to the same figure.

    Weeks and months cut the same rows differently, so no total in one has to
    match a total in the other — but the whole statement does, and if it does not
    then the grouping is dropping rows or counting them twice.
    """
    monthly, weekly = run("current"), run("current", by="week")
    assert sum(p.total for p in weekly.periods) == sum(p.total for p in monthly.periods)
    assert weekly.rows_read == monthly.rows_read
    assert weekly.duplicates == monthly.duplicates
    assert weekly.transfers == monthly.transfers


def test_a_week_holds_the_month_end_together():
    # The boundary file has the 31st of January and the 1st of February in it.
    # They are different months and the same ISO week, which is the reason
    # somebody asks for a weekly view in the first place.
    week = next(p for p in run("boundary", by="week").periods if p.name == "2026-W05")
    assert "groceries" in week.by_category      # Tesco, made on the 31st
    assert "eating out" in week.by_category     # Pret, made on the 1st


def test_the_fixed_commitments_are_still_monthly_under_a_weekly_summary():
    # Recurring counts months whatever the grouping. Counted in weeks, a payment
    # made once a month would never appear in enough of them.
    assert run("current", by="week").recurring == run("current").recurring


# ── every statement ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["current", "other-bank", "boundary"])
def test_no_statement_ends_up_empty(name):
    assert len(run(name).text().strip()) > 100


@pytest.mark.parametrize("name", ["current", "other-bank", "boundary"])
@pytest.mark.parametrize("by", ["month", "week"])
def test_periods_come_out_in_order(name, by):
    # Weeks are the interesting half: they are labelled 2026-W03 and sorted as
    # text, which only holds because the number is padded and the year is the
    # ISO year.
    names = [p.name for p in run(name, by=by).periods]
    assert names == sorted(names)


@pytest.mark.parametrize("name", ["current", "other-bank", "boundary"])
def test_summarising_twice_gives_the_same_thing(name):
    raw = (FIXTURES / f"{name}.csv").read_text(encoding="utf-8")
    assert summarise(raw, RULES).text() == summarise(raw, RULES).text()


def test_an_empty_file_is_not_an_error():
    assert summarise("", RULES).rows_read == 0
    assert summarise("Date,Description,Amount\n", RULES).rows_read == 0
