"""The three sample statements, end to end.

Each exists to exercise something the others cannot. The current account has
duplicates, transfers, refunds and recurring payments; the other bank names its
columns differently and exports spending as positive; the boundary file has
transactions made in one month and posted in the next.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tally import settings
from tally.categories import Unmatched
from tally.summary import summarise

FIXTURES = Path(__file__).parent.parent / "fixtures"
SHIPPED = settings.load()

# current.csv carries a merchant nothing matches, on purpose. The rules as
# shipped stop for it, which is its own handful of tests at the bottom; every
# other test here is about something else, so they file it under a name instead.
RULES = replace(SHIPPED, unmatched="bucket")


RAW = {name: (FIXTURES / f"{name}.csv").read_text(encoding="utf-8")
       for name in ("current", "other-bank", "boundary")}


def run(name: str, by: str = "month"):
    return summarise(RAW[name], RULES, by=by)


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


def test_transfers_can_be_counted_as_spending_instead():
    # For somebody who thinks of money into savings as money out. The 600 comes
    # off January's total and appears as an ordinary category.
    result = summarise(RAW["current"], replace(RULES, show_transfers="spending"))
    january = next(p for p in result.periods if p.name == "2026-01")
    assert january.by_category["transfers"] == Decimal("-600.00")
    assert january.total == Decimal("515.46")      # 1115.46, less the 600 moved
    assert january.moved == Decimal("0")


def test_transfers_can_be_left_out_altogether():
    # What the tool used to do, for anybody who wants it back.
    result = summarise(RAW["current"], replace(RULES, show_transfers="never"))
    january = next(p for p in result.periods if p.name == "2026-01")
    assert january.total == Decimal("1115.46")
    assert january.moved == Decimal("0")
    assert "transfer" not in result.text().casefold()
    assert "transfer to savings" not in result.recurring


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

    Both summaries are made to merge duplicates the same way here, so that this
    is a test of the grouping alone. Under the rules as shipped they merge
    differently on purpose, and then the two are not expected to agree — see
    test_every_penny_the_weekly_summary_drops_is_one_it_listed.
    """
    alike = replace(RULES, duplicates={"month": "same wording", "week": "same wording"})
    monthly = summarise(RAW["current"], alike)
    weekly = summarise(RAW["current"], alike, by="week")
    assert sum(p.total for p in weekly.periods) == sum(p.total for p in monthly.periods)
    assert weekly.rows_read == monthly.rows_read
    assert weekly.duplicates == monthly.duplicates
    assert weekly.transfers == monthly.transfers


def test_a_week_is_where_the_bank_posted_it():
    """Tesco: made Saturday the 31st of January, posted Monday the 2nd.

    The month puts it in January, where the person spent it. The week puts it in
    the week the bank cleared it, which is the week it appears in on the
    statement they are holding it up against.
    """
    weeks = {p.name: p for p in run("boundary", by="week").periods}
    # Tesco 78.90 sits in the week it was posted, not the week it was made. The
    # week it was made still exists — Aldi 33.40 was posted inside it — and the
    # 78.90 is not in it.
    assert weeks["2026-W06"].by_category["groceries"] == Decimal("-78.90")
    assert weeks["2026-W05"].by_category["groceries"] == Decimal("-33.40")

    # And the month is unmoved: both were January spending, which is what the
    # monthly summary is for.
    months = {p.name: p for p in run("boundary").periods}
    assert months["2026-01"].by_category["groceries"] == Decimal("-112.30")


def test_a_week_with_nothing_in_it_but_a_transfer_still_appears():
    # A made-up statement, for the case the fixtures do not have: a quiet week
    # whose only event was moving money. Dropping the period would hide the
    # money again, exactly where it is easiest to hide.
    raw = ("Date,Description,Amount\n"
           "2026-01-05,TESCO,-10.00\n"
           "2026-01-14,Transfer to savings,-300.00\n")
    result = summarise(raw, RULES, by="week")
    week = next(p for p in result.periods if p.name == "2026-W03")
    assert week.by_category == {}
    assert week.moved == Decimal("-300.00")
    assert "## 2026-W03" in result.text()


def test_transfers_that_cancel_out_are_still_shown():
    """A bank that exports both legs of one move, in opposite directions.

    They net to nothing, and the line is printed anyway. Silence here would look
    exactly like a month with no transfers in it, which is the thing this was
    meant to stop.
    """
    raw = ("Date,Description,Amount\n"
           "2026-01-05,TESCO,-10.00\n"
           "2026-01-06,Transfer to savings,-300.00\n"
           "2026-01-06,Transfer from savings,300.00\n")
    result = summarise(raw, RULES)
    january = result.periods[0]
    assert january.transfers == 2
    assert january.moved == Decimal("0.00")
    assert "transfers              0.00" in result.text()


def test_the_fixed_commitments_are_still_monthly_under_a_weekly_summary():
    # Recurring counts months whatever the grouping. Counted in weeks, a payment
    # made once a month would never appear in enough of them.
    assert run("current", by="week").recurring == run("current").recurring


# ── the same shop written two ways ───────────────────────────────────────────

REWORDED = ("Transaction Date,Posting Date,Description,Amount\n"
            "2026-01-31,2026-02-02,TESCO STORES 3241,-52.40\n"    # Saturday, posts Monday
            "2026-01-31,2026-02-02,TESCO-EXPRESS 3241,-52.40\n"   # the same shop, reworded
            "2026-02-03,2026-02-03,PRET A MANGER,-4.85\n")


def test_a_weekly_shop_written_two_ways_is_one_shop_in_the_week():
    result = summarise(REWORDED, RULES, by="week")
    week = next(p for p in result.periods if p.name == "2026-W06")
    assert week.by_category["groceries"] == Decimal("-52.40")
    assert result.duplicates == 1


def test_and_two_shops_in_the_month_because_nothing_says_they_are_one():
    """The monthly summary keeps both, and that is not an oversight.

    Two rows for the same amount on the same day are more often two things than
    one thing written twice, and the monthly summary is the one being read for
    what was spent. The weekly one is being read against the bank's statement,
    where the bank has already decided they are one line.
    """
    result = summarise(REWORDED, RULES)
    january = next(p for p in result.periods if p.name == "2026-01")
    assert january.by_category["groceries"] == Decimal("-104.80")
    assert result.duplicates == 0


def test_every_penny_the_weekly_summary_drops_is_one_it_listed():
    """The two summaries disagree about the total, and the difference is shown.

    This is the cost of merging on the amount alone, and the reason the merges
    are printed: the weekly summary is lighter than the monthly one by exactly
    the rows it named, and by nothing else.
    """
    monthly = summarise(REWORDED, RULES)
    weekly = summarise(REWORDED, RULES, by="week")
    difference = (sum(p.total for p in weekly.periods)
                  - sum(p.total for p in monthly.periods))
    assert difference == -sum(m.dropped.amount for m in weekly.merged)
    assert difference == Decimal("52.40")


def test_what_was_merged_is_listed_with_both_descriptions():
    # The merge might be wrong — two coffees at the same price on the same day
    # look identical to one coffee written twice — so it is shown, not counted.
    out = summarise(REWORDED, RULES, by="week").text()
    assert "## Merged" in out
    assert "TESCO STORES 3241 / TESCO-EXPRESS 3241" in out
    assert "2026-02-02" in out       # the date it was matched on: the posting date


def test_an_exact_repeat_is_not_worth_listing():
    # Nothing to look at: the two rows said the same thing. current.csv has one.
    assert run("current").duplicates == 1
    assert "## Merged" not in run("current").text()


# ── a merchant with no rule ──────────────────────────────────────────────────

def test_a_merchant_nothing_matches_stops_the_run():
    with pytest.raises(Unmatched) as raised:
        summarise(RAW["current"], SHIPPED)
    assert "MOONLIGHT RECORDS" in str(raised.value)
    assert "line 15" in str(raised.value)


def test_every_unknown_merchant_is_listed_at_once():
    """Not the first one, all of them, with how often each appeared.

    Somebody adding rules wants the whole list in front of them. One run per
    missing merchant is the kind of job that gets abandoned halfway through.
    """
    raw = ("Date,Description,Amount\n"
           "2026-01-05,MOONLIGHT RECORDS,-24.00\n"
           "2026-01-06,QUEENS ARMS,-18.50\n"
           "2026-01-07,QUEENS ARMS,-22.10\n"
           "2026-01-08,TESCO,-10.00\n")
    with pytest.raises(Unmatched) as raised:
        summarise(raw, SHIPPED)
    message = str(raised.value)
    assert "MOONLIGHT RECORDS" in message and "QUEENS ARMS" in message
    assert "2 rows" in message              # the pub, twice
    assert "TESCO" not in message           # matched, so not anybody's problem
    assert 'unmatched = "bucket"' in message


def test_a_bucket_is_still_there_for_anybody_who_wants_it():
    # The old behaviour, one setting away: the merchant is filed under a name and
    # the summary is written. Visible in the output, but only if you look.
    result = summarise(RAW["current"], RULES)      # unmatched = "bucket"
    assert result.uncategorised == 1
    assert "uncategorised" in result.text()


def test_a_transfer_is_never_an_unknown_merchant():
    # "Transfer to savings" matches no merchant rule and never will. Stopping for
    # one would make the setting unusable for anybody who moves money.
    raw = ("Date,Description,Amount\n"
           "2026-01-05,TESCO,-10.00\n"
           "2026-01-06,Transfer to savings,-300.00\n")
    assert summarise(raw, SHIPPED).periods[0].moved == Decimal("-300.00")


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
    assert summarise(RAW[name], RULES).text() == summarise(RAW[name], RULES).text()


def test_an_empty_file_is_not_an_error():
    assert summarise("", RULES).rows_read == 0
    assert summarise("Date,Description,Amount\n", RULES).rows_read == 0
