"""Each rule, on its own.

One test per policy, plus the cases where two policies meet. The interesting ones
are at the bottom: those are where a change to one rule breaks another.

Every rule is handed its settings, so each of these says which rules it is
running under. `RULES` is the set that ships in `tally/rules.toml`; the tests
that care about a setting build their own rather than asserting on whatever the
shipped file happens to say today.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tally import categories, dedupe, money, periods, recurring, rules
from tally.rows import Row, parse_amount, parse_date, read

RULES = rules.default()


def row(description="TESCO STORES", amount="-10.00", made="2026-01-05", posted=None):
    made_date = date.fromisoformat(made)
    return Row(
        made=made_date,
        posted=date.fromisoformat(posted) if posted else made_date,
        description=description,
        amount=Decimal(amount),
    )


def rules_from(body: str):
    """A rules file written inline, for the tests that vary one setting."""
    return rules.parse(body)


MINIMAL = """
[periods]
month = "made"
week = "posted"
[duplicates]
match = ["date", "amount"]
[categories]
unmatched = "stop"
[recurring]
months = 3
[money]
positive_share = 0.8
[transfers]
words = ["transfer"]
handling = "show"
[[rule]]
pattern = 'tesco'
category = "groceries"
"""


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


# ── the rules file ───────────────────────────────────────────────────────────

def test_the_shipped_rules_load():
    assert RULES.categories
    assert RULES.recurring_months >= 1
    assert 0 < RULES.positive_share < 1
    assert RULES.transfer_words


def test_the_order_in_the_file_is_the_order_they_are_tried():
    # The whole reason the file uses [[rule]] rather than one table. "shell
    # energy" has to stay above "shell", and this fails if a reader tidies the
    # file into alphabetical order.
    names = RULES.category_names()
    assert names.index("utilities") < names.index("fuel")


def test_a_merchant_can_be_added_without_touching_code():
    # The point of the whole exercise.
    added = rules_from(MINIMAL + "\n[[rule]]\npattern = 'moonlight'\ncategory = \"music\"\n")
    assert categories.categorise(row("MOONLIGHT RECORDS"), added) == "music"
    assert categories.categorise(row("MOONLIGHT RECORDS"), RULES) == "uncategorised"


def test_a_rules_file_that_cannot_be_used_stops_the_run():
    # Rather than falling back to something built in. Summarising against rules
    # other than the ones in the file you are reading is wrong in a way that is
    # very hard to see.
    with pytest.raises(rules.RulesError):
        rules_from("[recurring]\nmonths = 3\n")            # no [[rule]] entries
    with pytest.raises(rules.RulesError):
        rules_from(MINIMAL + "\n[[rule]]\npattern = '('\ncategory = \"x\"\n")


def test_a_broken_rules_file_says_what_is_wrong_with_it():
    with pytest.raises(rules.RulesError) as caught:
        rules_from(MINIMAL.replace("months = 3", "months = 0"))
    assert "months" in str(caught.value)


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


def test_a_transaction_belongs_to_the_week_it_was_made():
    assert periods.week_of(row(made="2026-01-05")) == "2026-W02"


def test_a_week_is_labelled_with_its_iso_year_not_its_calendar_year():
    """The 29th of December 2025 is in ISO week 1 of 2026.

    Labelling it with the calendar year would give `2025-W01`, filing it beside
    the January eleven months earlier and sorting it to the wrong end of the
    summary.
    """
    assert periods.week_of(row(made="2025-12-29")) == "2026-W01"
    assert periods.week_of(row(made="2027-01-03")) == "2026-W53"


def test_a_week_can_span_two_months():
    # Which is the point of asking for weeks, and the reason netting within a
    # period is worth being careful about.
    assert periods.week_of(row(made="2026-01-31")) == periods.week_of(row(made="2026-02-01"))


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


def test_the_wording_that_means_a_transfer_comes_from_the_rules():
    mine = rules_from(MINIMAL.replace('words = ["transfer"]', 'words = ["moved to pot"]'))
    assert dedupe.is_transfer(row("Moved to pot"), mine)
    assert not dedupe.is_transfer(row("Transfer to savings"), mine)


def test_what_happens_to_a_transfer_comes_from_the_rules():
    assert rules_from(MINIMAL).shows_transfers
    assert not rules_from(MINIMAL.replace('handling = "show"', 'handling = "hide"')).shows_transfers


def test_a_handling_that_is_not_a_mode_is_refused():
    # Rather than falling through to whichever branch the code happens to take,
    # which would silently hide the transfers a typo was meant to show.
    with pytest.raises(rules.RulesError) as caught:
        rules_from(MINIMAL.replace('handling = "show"', 'handling = "shwo"'))
    assert "shwo" in str(caught.value)


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


def test_how_many_months_it_takes_comes_from_the_rules():
    # The same two rows, under a file that says two is enough.
    rows = [row("NETFLIX.COM", "-10.99", f"2026-0{m}-09") for m in (1, 2)]
    lenient = rules_from(MINIMAL.replace("months = 3", "months = 2"))
    assert recurring.find(rows, RULES) == set()
    assert "netflix.com" in recurring.find(rows, lenient)


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


def test_the_share_that_means_a_flipped_export_comes_from_the_rules():
    # Six positive out of ten. Under the shipped 0.8 that is an ordinary export
    # with some income in it; under 0.5 it is a flipped one.
    def ten():
        return [row(amount="10.00") for _ in range(6)] + [row(amount="-10.00") for _ in range(4)]

    ordinary, flipped = ten(), ten()
    money.sign_convention(ordinary, RULES)
    money.sign_convention(flipped, rules_from(MINIMAL.replace("0.8", "0.5")))
    assert ordinary[0].amount == Decimal("10.00")
    assert flipped[0].amount == Decimal("-10.00")


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
