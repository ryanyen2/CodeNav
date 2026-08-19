"""Each rule, on its own.

One test per policy, plus the cases where two policies meet. The interesting ones
are at the bottom: those are where a change to one rule breaks another.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tally import categories, dedupe, money, periods, recurring, settings
from tally.rows import Row, parse_amount, parse_date, read

# The rules the tool ships with. Read once: every test below is handed these
# rather than reaching for a constant inside a module, which is the whole point
# of the settings being an argument.
RULES = settings.load()


def row(description="TESCO STORES", amount="-10.00", made="2026-01-05", posted=None):
    made_date = date.fromisoformat(made)
    return Row(
        made=made_date,
        posted=date.fromisoformat(posted) if posted else made_date,
        description=description,
        amount=Decimal(amount),
    )


# ── reading the export ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["2026-01-05", "05/01/2026", "5 Jan 2026"])
def test_the_date_formats_banks_use(text):
    assert parse_date(text) is not None


@pytest.mark.parametrize("text,expected", [
    ("-52.40", "-52.40"), ("£52.40", "52.40"), ("1,204.00", "1204.00"), ("(52.40)", "-52.40"),
])
def test_the_amount_formats_banks_use(text, expected):
    assert parse_amount(text) == Decimal(expected)


def test_a_transaction_date_beats_a_posting_date():
    # A bank exporting both would otherwise give the posting date, and every
    # transaction near a month end would shift into the wrong month.
    rows = read("Posting Date,Transaction Date,Description,Amount\n"
                "2026-02-02,2026-01-31,TESCO,-10.00\n")
    assert rows[0].made == date(2026, 1, 31)
    assert rows[0].posted == date(2026, 2, 2)


def test_an_unreadable_row_is_skipped_not_guessed_at():
    rows = read("Date,Description,Amount\n2026-01-05,TESCO,-10.00\nnonsense,TESCO,abc\n")
    assert len(rows) == 1


# ── categories ───────────────────────────────────────────────────────────────

def test_the_first_matching_rule_wins():
    # "shell energy" is above "shell" on purpose. The other order puts the
    # electricity bill in the car.
    assert categories.categorise(row("SHELL ENERGY"), RULES) == "utilities"
    assert categories.categorise(row("SHELL 4417"), RULES) == "fuel"


def test_anything_unmatched_goes_to_a_bucket():
    # Rather than stopping the run. A summary with an uncategorised line is
    # useful; a summary that refused to be written is not.
    assert categories.categorise(row("MOONLIGHT RECORDS"), RULES) == "uncategorised"


# ── which period ─────────────────────────────────────────────────────────────

def test_which_date_decides_the_period_is_a_setting():
    """Made on the 31st, posted on the 2nd — the two dates disagree.

    Filed by the date it was made it is January spending, which is what the
    person remembers. Filed by the date it posted it is February, which is where
    the bank's own statement shows it. rules.toml picks, per summary.
    """
    late = row(made="2026-01-31", posted="2026-02-02")
    assert periods.label_for("month", "made")(late) == "2026-01"
    assert periods.label_for("month", "posted")(late) == "2026-02"
    assert periods.label_for("week", "made")(late) == "2026-W05"
    assert periods.label_for("week", "posted")(late) == "2026-W06"


def test_refunds_net_against_the_category():
    assert periods.net([Decimal("-30.00"), Decimal("10.00")]) == Decimal("-20.00")


def test_a_week_is_an_iso_week():
    # The 5th of January 2026 is a Monday, so it opens week 02.
    assert periods.week_of(date(2026, 1, 5)) == "2026-W02"


def test_a_week_belongs_to_its_iso_year_not_its_calendar_year():
    """New Year's Day 2027 is a Friday, so it falls in week 53 of 2026.

    The ISO year is used rather than the date's own year. Using the calendar year
    would label this `2027-W53`: a week that sorts after every real week of 2027
    while holding the first day of it, so January would end up at the bottom of
    the summary.
    """
    assert periods.week_of(date(2027, 1, 1)) == "2026-W53"
    assert periods.week_of(date(2025, 12, 29)) == "2026-W01"


def test_weeks_sort_in_the_order_they_happened():
    days = [date(2025, 12, 29), date(2026, 1, 5), date(2027, 1, 1)]
    labels = [periods.week_of(day) for day in days]
    assert labels == sorted(labels)


def test_an_unknown_period_is_refused_by_name():
    with pytest.raises(ValueError) as raised:
        periods.label_for("fortnight", "made")
    assert "month" in str(raised.value) and "week" in str(raised.value)


def test_an_unknown_date_is_refused_by_name():
    with pytest.raises(ValueError) as raised:
        periods.label_for("week", "cleared")
    assert "made" in str(raised.value) and "posted" in str(raised.value)


def test_the_periods_on_offer_are_the_ones_the_settings_check_for():
    # Two lists, in two modules, that have to say the same thing: settings.py
    # validates rules.toml against PERIOD_NAMES, periods.py implements PERIODS.
    assert sorted(periods.PERIODS) == sorted(settings.PERIOD_NAMES)


# ── duplicates and transfers ─────────────────────────────────────────────────

def test_the_same_transaction_twice_is_dropped_once():
    kept, merges = dedupe.drop_duplicates([row(), row()], RULES, "month")
    assert len(kept) == 1 and len(merges) == 1


def test_two_transactions_that_differ_are_both_kept():
    pair = [row(amount="-10.00"), row(amount="-11.00")]
    kept, merges = dedupe.drop_duplicates(pair, RULES, "month")
    assert len(kept) == 2 and merges == []


def test_the_same_shop_written_two_ways_is_two_things_or_one_by_setting():
    """The same amount on the same day, worded differently.

    Under "same wording" they are two purchases that happened to cost the same.
    Under "any wording" they are one purchase the bank wrote twice. Neither can
    be told from the other by looking, which is why it is a setting and why what
    it merges is listed in the summary.
    """
    pair = [row("TESCO STORES 3241"), row("TESCO-EXPRESS 3241")]

    kept, merges = dedupe.drop_duplicates(pair, RULES, "month")     # same wording
    assert len(kept) == 2 and merges == []

    kept, merges = dedupe.drop_duplicates(pair, RULES, "week")      # any wording
    assert len(kept) == 1
    assert [m.reworded for m in merges] == [True]
    assert merges[0].kept.description == "TESCO STORES 3241"
    assert merges[0].dropped.description == "TESCO-EXPRESS 3241"


def test_the_day_compared_is_the_one_that_summary_is_lined_up_on():
    # Posted the same day, made a day apart. The weekly summary is lined up on
    # the posting date, so to it these are one transaction.
    pair = [row(made="2026-01-05", posted="2026-01-07"),
            row(made="2026-01-06", posted="2026-01-07")]
    assert len(dedupe.drop_duplicates(pair, RULES, "month")[0]) == 2
    assert len(dedupe.drop_duplicates(pair, RULES, "week")[0]) == 1


def test_a_transfer_is_recognised_by_its_wording():
    assert dedupe.is_transfer(row("Transfer to savings"), RULES)
    assert not dedupe.is_transfer(row("TESCO STORES"), RULES)


# ── recurring ────────────────────────────────────────────────────────────────

def test_a_payment_in_three_months_at_one_amount_is_recurring():
    rows = [row("NETFLIX.COM", "-10.99", f"2026-0{m}-09") for m in (1, 2, 3)]
    assert "netflix.com" in recurring.find(rows, RULES)


def test_the_same_merchant_at_different_amounts_is_not():
    # Merchant alone would call a supermarket recurring, which is true and
    # useless. The point is the fixed commitments.
    rows = [row("TESCO STORES", amt, f"2026-0{m}-09")
            for m, amt in zip((1, 2, 3), ("-52.40", "-47.15", "-61.05"))]
    assert recurring.find(rows, RULES) == set()


def test_two_months_is_not_enough():
    rows = [row("NETFLIX.COM", "-10.99", f"2026-0{m}-09") for m in (1, 2)]
    assert recurring.find(rows, RULES) == set()


# ── money ────────────────────────────────────────────────────────────────────

def test_rounding_happens_once_at_the_total():
    # Three rows that each round up would total a penny more than they should.
    amounts = [Decimal("0.334"), Decimal("0.334"), Decimal("0.334")]
    assert money.round_total(sum(amounts, Decimal("0"))) == Decimal("1.00")


def test_an_export_with_spending_as_positive_is_flipped():
    rows = [row(amount="10.00") for _ in range(10)]
    money.sign_convention(rows, RULES)
    assert all(r.amount < 0 for r in rows)


def test_an_ordinary_export_is_left_alone():
    rows = [row(amount="-10.00") for _ in range(9)] + [row("SALARY", "2400.00")]
    money.sign_convention(rows, RULES)
    assert rows[0].amount == Decimal("-10.00")


# ── where two rules meet ─────────────────────────────────────────────────────

def test_a_transfer_pair_is_not_a_duplicate():
    """The coupled pair, and the reason drop_duplicates is not three lines.

    A transfer between two of your own accounts exports as two rows on the same
    day, for the same amount, with the same wording. That is exactly the shape of
    a duplicate. Dropping one leg leaves a lone entry that looks like a real
    payment, and the money appears to have gone somewhere it did not.
    """
    pair = [row("Transfer to savings", "-300.00"), row("Transfer to savings", "-300.00")]
    for by in ("month", "week"):
        kept, merges = dedupe.drop_duplicates(pair, RULES, by)
        assert len(kept) == 2, f"both legs of the transfer survive, by {by}"
        assert merges == []

    # And an ordinary pair with the same shape is still a duplicate.
    ordinary = [row("TESCO STORES", "-300.00"), row("TESCO STORES", "-300.00")]
    kept, merges = dedupe.drop_duplicates(ordinary, RULES, "month")
    assert len(kept) == 1 and len(merges) == 1
