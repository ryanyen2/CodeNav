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

def test_a_transaction_belongs_to_the_month_it_was_made():
    # Made on the 31st, posted on the 2nd. It is January spending.
    assert periods.month_of(row(made="2026-01-31", posted="2026-02-02")) == "2026-01"


def test_refunds_net_against_the_category():
    assert periods.net([Decimal("-30.00"), Decimal("10.00")]) == Decimal("-20.00")


# ── duplicates and transfers ─────────────────────────────────────────────────

def test_the_same_transaction_twice_is_dropped_once():
    kept, dropped = dedupe.drop_duplicates([row(), row()], RULES)
    assert len(kept) == 1 and dropped == 1


def test_two_transactions_that_differ_are_both_kept():
    kept, dropped = dedupe.drop_duplicates([row(amount="-10.00"), row(amount="-11.00")], RULES)
    assert len(kept) == 2 and dropped == 0


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
    kept, dropped = dedupe.drop_duplicates(pair, RULES)
    assert len(kept) == 2, "both legs of the transfer survive"
    assert dropped == 0

    # And an ordinary pair with the same shape is still a duplicate.
    ordinary = [row("TESCO STORES", "-300.00"), row("TESCO STORES", "-300.00")]
    kept, dropped = dedupe.drop_duplicates(ordinary, RULES)
    assert len(kept) == 1 and dropped == 1
