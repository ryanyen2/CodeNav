"""Which period a transaction belongs to, and what a refund does to it.

Three policies live here: how a month is labelled, how a week is labelled, and
what a refund does to the category it came from. Which date decides — the date
the payment was made or the date the bank posted it — is in rules.toml, and is
set for each summary separately, because the two summaries are read for
different reasons.

This was months.py, and grew weeks when the summary did.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

from .rows import Row
from .settings import DATES


def month_of(day: date) -> str:
    """The month a date falls in."""
    return f"{day.year:04d}-{day.month:02d}"


def week_of(day: date) -> str:
    """The week a date falls in, as `2026-W03`.

    ISO weeks: Monday to Sunday, and week 01 is the one holding the first
    Thursday of the year. That is why the year here is the ISO year and not
    `day.year` — the 1st of January can fall in week 52 of the year before, and
    using the calendar year would label it `2026-W53`, a week that sorts before
    every other week of 2026 and belongs to neither year.

    The alternative — weeks numbered from the 1st of January, so week 01 is the
    1st to the 7th — keeps every week inside its own year, at the cost of a short
    week every January and weeks that start on a different day each year.
    """
    year, week, _ = day.isocalendar()
    return f"{year:04d}-W{week:02d}"


# The summaries that can be asked for, and how each labels a date. settings.py
# checks rules.toml against these names; adding another would be adding a
# function above, a line here, and a line to PERIOD_NAMES in settings.py.
PERIODS: dict[str, Callable[[date], str]] = {
    "month": month_of,
    "week": week_of,
}


def label_for(period: str, dated: str) -> Callable[[Row], str]:
    """How to label a row, for one summary.

    `dated` is "made" or "posted", and is the whole of the difference between a
    summary that answers "what did I spend in January" and one that agrees with
    the statement the bank sends.

    A card payment made on the 31st can post on the 2nd. Filed by the date it was
    made, it is January spending, which is what the person remembers doing. Filed
    by the date it posted, it is where the bank puts it, which is what matters
    when the two are being read side by side. Neither is wrong; they answer
    different questions, so rules.toml sets it per summary.

    Looked up once, before any row is read, so an unknown name is an error even
    on an empty file — rather than a summary that quietly comes back with no
    periods in it and looks like a statement with nothing in it.
    """
    if period not in PERIODS:
        raise ValueError(
            f"no such period: {period!r}; try one of {', '.join(sorted(PERIODS))}"
        )
    if dated not in DATES:
        raise ValueError(f"no such date: {dated!r}; try one of {', '.join(DATES)}")
    label = PERIODS[period]
    return lambda row: label(getattr(row, dated))


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
