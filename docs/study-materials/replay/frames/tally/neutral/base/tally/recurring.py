"""Payments that come round every month.

One policy lives here.
"""
from __future__ import annotations

from collections import defaultdict

from .rows import Row

# How many months a payment has to appear in before it counts as recurring. Two
# is a coincidence often enough to be annoying; three is not.
MONTHS = 3


def find(rows: list[Row]) -> set[str]:
    """Descriptions that recur monthly at the same amount.

    Both the merchant AND the amount have to match. Merchant alone would call a
    supermarket recurring, which is true and useless — the point is to find the
    fixed commitments, the ones the same every month whether or not you thought
    about them.
    """
    by_pair: dict[tuple[str, object], set[str]] = defaultdict(set)
    for row in rows:
        pair = (row.description.casefold(), row.amount)
        by_pair[pair].add(f"{row.made.year:04d}-{row.made.month:02d}")
    return {desc for (desc, _), months in by_pair.items() if len(months) >= MONTHS}
