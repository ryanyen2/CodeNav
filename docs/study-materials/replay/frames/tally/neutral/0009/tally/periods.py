"""Which period a transaction belongs to, and what a refund does to it.

Three policies live here: which date decides the period, how a week is counted,
and what a refund does to the category it came from.

This was months.py, and grew weeks when the summary did. The choice of month or
week is made once in summary.py and everything below works on the label this
hands back, so nothing else has to know which one is in use.
"""
from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from .rows import Row


def month_of(row: Row) -> str:
    """The month a transaction belongs to.

    The date it was MADE, not the date it posted. A card payment on the 31st can
    post on the 2nd, and filing it in the wrong month means a summary that does
    not match what the person remembers doing. The alternative — the posting date
    — is what the bank's own statement uses, and is the right answer if you are
    reconciling against the bank rather than against your memory.
    """
    return f"{row.made.year:04d}-{row.made.month:02d}"


def week_of(row: Row) -> str:
    """The week a transaction belongs to, as `2026-W03`.

    The date it was made, for the same reason as the month.

    ISO weeks: Monday to Sunday, and week 01 is the one holding the first
    Thursday of the year. That is why the year here is the ISO year and not
    `row.made.year` — the 1st of January can fall in week 52 of the year before,
    and using the calendar year would file it as `2026-W53`, a week that sorts
    before every other week of 2026 and belongs to neither year.

    The alternative — weeks numbered from the 1st of January, so week 01 is the
    1st to the 7th — keeps every week inside its own year, at the cost of a short
    week every January and weeks that start on a different day each year.
    """
    year, week, _ = row.made.isocalendar()
    return f"{year:04d}-W{week:02d}"


# The periods a summary can be grouped by, and the label each one puts on a
# bucket. summary.py looks a name up here; adding another would be adding a
# function above and a line to this table.
PERIODS: dict[str, Callable[[Row], str]] = {
    "month": month_of,
    "week": week_of,
}


def label_for(period: str) -> Callable[[Row], str]:
    """The function that labels a row, for the period a summary is grouped by.

    Looked up once, before any row is read, so that an unknown period is an error
    even on an empty file — rather than a summary that quietly comes back with no
    periods in it and looks like a statement with nothing in it.
    """
    try:
        return PERIODS[period]
    except KeyError:
        raise ValueError(
            f"no such period: {period!r}; try one of {', '.join(sorted(PERIODS))}"
        ) from None


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
    """
    return sum(amounts, Decimal("0"))
