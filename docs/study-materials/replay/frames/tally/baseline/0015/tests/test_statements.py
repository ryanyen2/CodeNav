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


def test_transfers_are_left_out_of_the_spending():
    out = run("current").text()
    assert "transfer" not in out.casefold()


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


# ── every statement ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["current", "other-bank", "boundary"])
def test_no_statement_ends_up_empty(name):
    assert len(run(name).text().strip()) > 100


@pytest.mark.parametrize("name", ["current", "other-bank", "boundary"])
def test_months_come_out_in_order(name):
    names = [p.name for p in run(name).periods]
    assert names == sorted(names)


@pytest.mark.parametrize("name", ["current", "other-bank", "boundary"])
def test_summarising_twice_gives_the_same_thing(name):
    raw = (FIXTURES / f"{name}.csv").read_text(encoding="utf-8")
    assert summarise(raw, RULES).text() == summarise(raw, RULES).text()


def test_an_empty_file_is_not_an_error():
    assert summarise("", RULES).rows_read == 0
    assert summarise("Date,Description,Amount\n", RULES).rows_read == 0
