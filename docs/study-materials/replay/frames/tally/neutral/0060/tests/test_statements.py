"""The sample statements, end to end.

Each exists to exercise something the others cannot. The current account has
duplicates, transfers, refunds and recurring payments; the other bank names its
columns differently and exports spending as positive; the boundary file has
transactions made in one month and posted in the next; one-shop-three-ways has
one purchase written three ways, and two different purchases that look alike.

`current.csv` still holds a merchant no rule covers, on purpose, so most of
these run with `unmatched = "bucket"`. The shipped default is `"stop"`, and the
tests for that are in their own section below.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tally import categories, dedupe, money, periods, rules
from tally.rows import read
from tally.summary import summarise

FIXTURES = Path(__file__).parent.parent / "fixtures"
ALL = ["current", "other-bank", "boundary", "one-shop-three-ways"]

# The shipped rules, with the unknown merchant filed rather than refused.
BUCKET = replace(rules.default(), unmatched=rules.BUCKET)

# And with transfers hidden again, for the tests that check that showing them is
# a change of report and not a change of arithmetic.
HIDDEN = replace(BUCKET, transfer_handling=rules.HIDE)


def run(name: str, by: str = "month", using=BUCKET):
    return summarise((FIXTURES / f"{name}.csv").read_text(encoding="utf-8"), rules=using, by=by)


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
    hidden, shown = run("current", using=HIDDEN), run("current")
    assert [p.total for p in hidden.periods] == [p.total for p in shown.periods]
    assert hidden.transfers == shown.transfers == 4
    assert "moved" not in hidden.text()


def test_a_period_of_nothing_but_transfers_still_gets_a_section():
    # Otherwise the one thing that happened that week is the one thing missing.
    only = "Date,Description,Amount\n2026-01-04,Transfer to savings,-300.00\n"
    period = summarise(only, rules=BUCKET).periods[0]
    assert period.moved == Decimal("-300.00")
    assert period.total == Decimal("0")


def test_a_refund_nets_within_its_own_month_and_not_across_months():
    """Boots: 8.60 out in January, 8.60 back in February.

    Netting happens inside a period, so January still shows the spend and
    February shows the money back. That is a real limitation rather than a bug:
    a summary of what you spent in January should say what you spent in January,
    and a refund that arrived in February is a February event.
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


# ── the other bank ───────────────────────────────────────────────────────────

def test_different_column_names_still_read():
    assert run("other-bank").rows_read == 13


def test_spending_exported_as_positive_is_flipped():
    for period in run("other-bank").periods:
        assert period.total < 0


def test_an_export_with_no_posting_date_falls_back_to_the_one_it_has():
    # other-bank.csv has a Date column and nothing else, so asking for weeks on
    # the posting date is harmless rather than wrong.
    assert rules.default().date_for("week") == rules.POSTED
    rows = read((FIXTURES / "other-bank.csv").read_text(encoding="utf-8"))
    assert all(row.posted == row.made for row in rows)
    assert run("other-bank", by="week").periods


# ── which date a period is lined up on ───────────────────────────────────────

def test_months_are_lined_up_on_the_date_it_was_made():
    # Aldi, Tesco and Shell: 33.40 + 78.90 + 60.00, two of them posted in
    # February. By month, what you remember doing in January.
    january = next(p for p in run("boundary").periods if p.name == "2026-01")
    assert january.total == Decimal("-172.30")


def test_and_the_ones_made_in_february_are_february():
    february = next(p for p in run("boundary").periods if p.name == "2026-02")
    assert february.total == Decimal("-78.00")


def test_weeks_are_lined_up_on_the_date_the_bank_posted():
    """The boundary file, cut the way the bank sees it.

    Tesco and Shell were made on the 31st of January and posted on the 2nd and
    3rd of February; Pret was made on the 1st of February and posted on the 2nd.
    All three reached the account in ISO week 6, which is the week they appear on
    the statement, and so the week they are reported in.
    """
    weekly = run("boundary", by="week")
    week = next(p for p in weekly.periods if p.name == "2026-W06")
    assert week.by_category["groceries"] == Decimal("-78.90")     # made 31 Jan
    assert week.by_category["fuel"] == Decimal("-60.00")          # made 31 Jan
    assert week.by_category["eating out"] == Decimal("-4.85")     # made 1 Feb


def test_the_two_datings_really_do_disagree():
    # Otherwise the setting would be doing nothing and the test above would pass
    # for the wrong reason.
    made = replace(BUCKET, period_dates={"month": rules.MADE, "week": rules.MADE})
    assert [p.name for p in run("boundary", by="week", using=made)] != \
           [p.name for p in run("boundary", by="week")]


def test_a_grouping_can_be_put_back_on_the_made_date():
    made = replace(BUCKET, period_dates={"month": rules.MADE, "week": rules.MADE})
    week = next(p for p in run("boundary", by="week", using=made).periods if p.name == "2026-W05")
    assert week.by_category["fuel"] == Decimal("-60.00")          # made 31 Jan


# ── one shop written three ways ──────────────────────────────────────────────

def test_one_purchase_written_three_ways_is_counted_once():
    # Three spellings of the same Tesco shop on the same day for the same money.
    result = run("one-shop-three-ways")
    assert result.duplicates == 3
    assert result.periods[0].by_category["groceries"] == Decimal("-52.40")


def test_and_the_price_of_that_is_two_real_purchases_becoming_one():
    """The cost of leaving the description out of the match, in one assertion.

    A Costa and a Greggs, both 3.40, both on the 7th. Nothing in the fields being
    compared tells them apart, so one is dropped and 3.40 of real spending goes
    missing. This is not a bug to be fixed; it is the trade the setting makes,
    and the number of dropped rows is on the line every run prints.
    """
    loose = run("one-shop-three-ways")
    assert loose.periods[0].by_category["eating out"] == Decimal("-3.40")

    strict = replace(BUCKET, duplicate_match=("date", "amount", "description"))
    kept = run("one-shop-three-ways", using=strict)
    assert kept.periods[0].by_category["eating out"] == Decimal("-6.80")
    # And the same setting triple counts the shop that the loose one got right.
    assert kept.periods[0].by_category["groceries"] == Decimal("-157.20")


def test_the_duplicate_rule_reports_what_it_dropped():
    # The only way to notice the trade above.
    assert "3 duplicates" in run("one-shop-three-ways").line()


# ── a merchant no rule covers ────────────────────────────────────────────────

def test_an_unknown_merchant_stops_the_run():
    with pytest.raises(categories.Unmatched) as caught:
        run("current", using=rules.default())
    assert caught.value.descriptions == ["MOONLIGHT RECORDS"]


def test_every_unknown_merchant_is_named_at_once():
    # Rather than one per run, which would make adding a month of new shops a
    # matter of running it once per shop.
    raw = ("Date,Description,Amount\n"
           "2026-01-05,ZZZ ONE,-1.00\n2026-01-06,AAA TWO,-2.00\n2026-01-07,ZZZ ONE,-3.00\n")
    with pytest.raises(categories.Unmatched) as caught:
        summarise(raw)
    assert caught.value.descriptions == ["AAA TWO", "ZZZ ONE"]
    assert "AAA TWO" in str(caught.value) and "ZZZ ONE" in str(caught.value)


def test_the_bucket_is_still_there_for_anybody_who_wants_it():
    result = run("current")
    assert result.uncategorised == 1
    assert "uncategorised" in result.text()


def test_nothing_is_written_when_the_run_stops():
    # The exception comes before any period is built, so there is no half a
    # summary to mistake for a whole one.
    with pytest.raises(categories.Unmatched):
        run("current", using=rules.default())


# ── every statement ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ALL)
@pytest.mark.parametrize("by", ["month", "week"])
def test_the_summary_accounts_for_every_row_it_kept(name, by):
    """total + moved == every row that survived deduping, period by period.

    This is the property that makes the summary checkable against a statement.
    Duplicates are still excluded, deliberately and visibly -- the run says how
    many it dropped.
    """
    raw = (FIXTURES / f"{name}.csv").read_text(encoding="utf-8")
    rows = read(raw)
    money.sign_convention(rows, BUCKET)
    kept, _ = dedupe.drop_duplicates(rows, BUCKET)

    period_of = periods.grouping(by, BUCKET)
    expected: dict[str, Decimal] = {}
    for row in kept:
        expected[period_of(row)] = expected.get(period_of(row), Decimal("0")) + row.amount

    for period in run(name, by).periods:
        assert period.total + period.moved == expected[period.name], period.name


@pytest.mark.parametrize("name", ALL)
def test_the_same_money_is_in_both_cuts(name):
    # Grouping is the only thing --by-week changes, even now that the two cuts
    # are lined up on different dates. Every row still lands in exactly one
    # period either way, so the two shapes agree on the total.
    monthly, weekly = run(name), run(name, by="week")
    assert sum(p.total for p in weekly.periods) == sum(p.total for p in monthly.periods)
    assert sum(p.moved for p in weekly.periods) == sum(p.moved for p in monthly.periods)
    assert weekly.rows_read == monthly.rows_read
    assert weekly.duplicates == monthly.duplicates
    assert weekly.transfers == monthly.transfers


@pytest.mark.parametrize("name", ALL)
def test_recurring_stays_monthly_however_the_spending_is_cut(name):
    assert run(name, by="week").recurring == run(name).recurring


def test_asking_for_a_grouping_that_does_not_exist_is_an_error():
    with pytest.raises(ValueError):
        summarise("", by="fortnight")


@pytest.mark.parametrize("name", ALL)
@pytest.mark.parametrize("by", ["month", "week"])
def test_no_statement_ends_up_empty(name, by):
    assert len(run(name, by).text().strip()) > 100


@pytest.mark.parametrize("name", ALL)
@pytest.mark.parametrize("by", ["month", "week"])
def test_periods_come_out_in_order(name, by):
    names = [p.name for p in run(name, by).periods]
    assert names == sorted(names)


@pytest.mark.parametrize("name", ALL)
def test_summarising_twice_gives_the_same_thing(name):
    raw = (FIXTURES / f"{name}.csv").read_text(encoding="utf-8")
    assert summarise(raw, rules=BUCKET).text() == summarise(raw, rules=BUCKET).text()


def test_an_empty_file_is_not_an_error():
    assert summarise("").rows_read == 0
    assert summarise("Date,Description,Amount\n").rows_read == 0
