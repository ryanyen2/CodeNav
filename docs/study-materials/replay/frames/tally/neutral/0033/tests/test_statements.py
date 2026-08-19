"""The three sample statements, end to end.

Each exists to exercise something the others cannot. The current account has
duplicates, transfers, refunds and recurring payments; the other bank names its
columns differently and exports spending as positive; the boundary file has
transactions made in one month and posted in the next -- and, once weeks
existed, weeks that straddle a month end.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tally import dedupe, money, periods, rules
from tally.rows import read
from tally.summary import summarise

FIXTURES = Path(__file__).parent.parent / "fixtures"

# The shipped rules, with transfers hidden again, for the tests that check that
# showing them is a change of report and not a change of arithmetic.
HIDDEN = replace(rules.default(), transfer_handling=rules.HIDE)


def run(name: str, by: str = "month"):
    return summarise((FIXTURES / f"{name}.csv").read_text(encoding="utf-8"), by=by)


# ── the current account ──────────────────────────────────────────────────────

def test_the_repeated_shop_is_counted_once():
    assert run("current").duplicates == 1


def test_both_legs_of_every_transfer_survive():
    # Four transfer rows: a same-day pair in January, then one a month. If the
    # duplicate rule saw the pair, this would be three.
    assert run("current").transfers == 4


def test_transfers_are_reported_but_left_out_of_the_spending():
    """The 300 a month to savings, which used to be invisible.

    It is shown, because money that left the account and appears nowhere in the
    summary makes a total that cannot be reconciled against a statement. It is
    not in that total, because it was not spent -- it is still the account
    holder's money, and adding it would make a month of moving savings around
    look like a month of buying things.
    """
    result = run("current")
    january = next(p for p in result.periods if p.name == "2026-01")
    assert january.moved == Decimal("-600.00")
    assert "moved" in result.text()

    # No category picked it up, so the total is untouched by it.
    assert all("transfer" not in name.casefold() for name in january.by_category)
    assert january.total == sum(january.by_category.values(), Decimal("0"))


def test_hiding_transfers_changes_nothing_but_whether_they_are_shown():
    # The totals are the same either way. Only the extra line differs, which is
    # what makes "show" a safe default rather than a change of meaning.
    raw = (FIXTURES / "current.csv").read_text(encoding="utf-8")
    hidden = summarise(raw, rules=HIDDEN)
    shown = run("current")
    assert [p.total for p in hidden.periods] == [p.total for p in shown.periods]
    assert hidden.transfers == shown.transfers == 4
    assert "moved" not in hidden.text()
    assert all(p.moved == Decimal("0") for p in hidden.periods)


def test_a_period_of_nothing_but_transfers_still_gets_a_section():
    # Otherwise the one thing that happened that week is the one thing missing.
    only = "Date,Description,Amount\n2026-01-04,Transfer to savings,-300.00\n"
    period = summarise(only).periods[0]
    assert period.moved == Decimal("-300.00")
    assert period.total == Decimal("0")
    assert "moved" in summarise(only).text()


def test_a_refund_nets_within_its_own_month_and_not_across_months():
    """Boots: 8.60 out in January, 8.60 back in February.

    Netting happens inside a period, so January still shows the spend and
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
    for period in run("other-bank").periods:
        assert period.total < 0


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

def test_grouping_by_week_names_its_periods_as_weeks():
    result = run("current", by="week")
    assert result.period_kind == "week"
    assert all(p.name.startswith("2026-W") for p in result.periods)
    assert "weeks" in result.line()


def test_a_week_straddles_the_month_end_that_a_month_cannot():
    """The boundary file, cut the other way.

    Tesco and Shell were made on the 31st of January and Pret on the 1st of
    February. By month they are in two periods; by week they are in one, because
    that is the same Monday-to-Sunday week. This is the thing --by-week is for.
    """
    weekly = run("boundary", by="week")
    straddling = next(p for p in weekly.periods if p.name == "2026-W05")
    assert straddling.by_category["eating out"] == Decimal("-4.85")     # February
    assert straddling.by_category["fuel"] == Decimal("-60.00")          # January


@pytest.mark.parametrize("name", ["current", "other-bank", "boundary"])
def test_the_same_money_is_in_both_cuts(name):
    # Grouping is the only thing --by-week changes. Every row is still read, and
    # every penny still lands somewhere, so the two shapes have to agree on the
    # total. If this fails, weeks are losing or double-counting transactions.
    monthly, weekly = run(name), run(name, by="week")
    assert sum(p.total for p in weekly.periods) == sum(p.total for p in monthly.periods)
    assert weekly.rows_read == monthly.rows_read
    assert weekly.duplicates == monthly.duplicates
    assert weekly.transfers == monthly.transfers


@pytest.mark.parametrize("name", ["current", "other-bank", "boundary"])
@pytest.mark.parametrize("by", ["month", "week"])
def test_the_summary_accounts_for_every_row_it_kept(name, by):
    """total + moved == every row that survived deduping, period by period.

    This is the property that was missing. With transfers dropped silently there
    was money in the statement that appeared nowhere in the summary, and no way
    to check the figures against the bank short of adding the CSV up by hand.
    Duplicates are still excluded, deliberately and visibly -- the run says how
    many it dropped.
    """
    raw = (FIXTURES / f"{name}.csv").read_text(encoding="utf-8")
    rows = read(raw)
    money.sign_convention(rows, rules.default())
    kept, _ = dedupe.drop_duplicates(rows, rules.default())

    period_of = periods.OF[by]
    expected: dict[str, Decimal] = {}
    for row in kept:
        expected[period_of(row)] = expected.get(period_of(row), Decimal("0")) + row.amount

    for period in summarise(raw, by=by).periods:
        assert period.total + period.moved == expected[period.name], period.name


@pytest.mark.parametrize("name", ["current", "other-bank", "boundary"])
def test_recurring_stays_monthly_however_the_spending_is_cut(name):
    # "Comes round every month" does not become a different question because you
    # asked to see the spending seven days at a time.
    assert run(name, by="week").recurring == run(name).recurring


def test_asking_for_a_grouping_that_does_not_exist_is_an_error():
    with pytest.raises(ValueError):
        summarise("", by="fortnight")


# ── every statement ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["current", "other-bank", "boundary"])
@pytest.mark.parametrize("by", ["month", "week"])
def test_no_statement_ends_up_empty(name, by):
    assert len(run(name, by).text().strip()) > 100


@pytest.mark.parametrize("name", ["current", "other-bank", "boundary"])
@pytest.mark.parametrize("by", ["month", "week"])
def test_periods_come_out_in_order(name, by):
    # Week names sort correctly for the same reason month names do: the year is
    # first and both parts are zero padded.
    names = [p.name for p in run(name, by).periods]
    assert names == sorted(names)


@pytest.mark.parametrize("name", ["current", "other-bank", "boundary"])
def test_summarising_twice_gives_the_same_thing(name):
    raw = (FIXTURES / f"{name}.csv").read_text(encoding="utf-8")
    assert summarise(raw).text() == summarise(raw).text()


def test_an_empty_file_is_not_an_error():
    assert summarise("").rows_read == 0
    assert summarise("Date,Description,Amount\n").rows_read == 0
