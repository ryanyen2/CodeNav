"""Which period a transaction belongs to, and what a refund does to it.

Three policies live here: which date decides, how that date becomes the name of
a month or a week, and what happens when money comes back.

This was `months.py` until weeks were added. Nothing about picking the date or
netting a refund was ever specific to months, and once a summary could be
grouped either way, the old name described one of its two callers.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from .rows import Row


def _made(row: Row) -> date:
    """The date a transaction is filed under.

    The date it was MADE, not the date it posted. A card payment on the 31st can
    post on the 2nd, and filing it in the wrong month means a summary that does
    not match what the person remembers doing. The alternative — the posting date
    — is what the bank's own statement uses, and is the right answer if you are
    reconciling against the bank rather than against your memory.
    """
    return row.made


def month_of(row: Row) -> str:
    """The month a transaction belongs to, as `2026-01`."""
    made = _made(row)
    return f"{made.year:04d}-{made.month:02d}"


def week_of(row: Row) -> str:
    """The week a transaction belongs to, as `2026-W01`.

    ISO weeks, so a week runs Monday to Sunday and the first week of a year is
    the one holding its first Thursday. The alternative — seven-day bins counted
    from the first row in the file — would make the same transaction fall in a
    different week depending on what else you exported, and two statements could
    not be compared.

    The year here is the ISO year from `isocalendar`, NOT the calendar year, and
    the difference is the whole reason this is three lines. The 29th of December
    2025 is a Monday in ISO week 1 of 2026; labelling it `2025-W01` with its
    calendar year would file it beside the January before it and sort it to the
    wrong end of the summary.
    """
    year, week, _ = _made(row).isocalendar()
    return f"{year:04d}-W{week:02d}"


# Grouping by name, for the command line to choose between. The keys are what
# `--by-week` and the default select, and what the summary calls itself.
OF = {"month": month_of, "week": week_of}


def is_refund(row: Row) -> bool:
    """Money coming back, against something that went out.

    Recognised by sign alone, which is why income has to be excluded by category
    before this is asked: a salary is money in and is not a refund.
    """
    return row.amount > 0


def net(amounts: list[Decimal]) -> Decimal:
    """Refunds net against the category they came from.

    A returned jumper reduces what was spent on clothes, which is what somebody
    asking "what did I spend on clothes" means. Reporting them separately is
    defensible and is what a tax return wants, because there the gross figure
    matters.

    Netting happens WITHIN one period, never across two. A refund that arrives in
    a later month reduces that later month, and grouping by week makes this
    sharper rather than different: a purchase and its refund are more likely to
    land in two different weeks than in two different months. The alternative --
    carry a refund back to the period the purchase was in -- means a summary
    changes after it has been read, which is worse than one that is merely
    surprising.
    """
    return sum(amounts, Decimal("0"))
