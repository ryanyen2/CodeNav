"""Payments that come round every month.

One policy lives here. How many months make a payment recurring is in
rules.toml.
"""
from __future__ import annotations

from collections import defaultdict

from .rows import Row
from .settings import Settings


def find(rows: list[Row], settings: Settings) -> set[str]:
    """Descriptions that recur monthly at the same amount.

    Both the merchant AND the amount have to match. Merchant alone would call a
    supermarket recurring, which is true and useless — the point is to find the
    fixed commitments, the ones the same every month whether or not you thought
    about them.

    Months, whatever the summary is grouped by. A weekly summary still reports
    the monthly commitments, because that is what the word means here; counting
    in weeks would find almost nothing.
    """
    by_pair: dict[tuple[str, object], set[str]] = defaultdict(set)
    for row in rows:
        pair = (row.description.casefold(), row.amount)
        by_pair[pair].add(f"{row.made.year:04d}-{row.made.month:02d}")
    return {desc for (desc, _), months in by_pair.items() if len(months) >= settings.recurring_months}
