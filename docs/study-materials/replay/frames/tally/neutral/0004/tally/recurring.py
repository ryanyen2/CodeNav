"""Payments that come round every month.

One policy lives here. How many months it takes to count is in `rules.toml`.
"""
from __future__ import annotations

from collections import defaultdict

from .rows import Row
from .rules import Rules


def find(rows: list[Row], rules: Rules) -> set[str]:
    """Descriptions that recur monthly at the same amount.

    Both the merchant AND the amount have to match. Merchant alone would call a
    supermarket recurring, which is true and useless -- the point is to find the
    fixed commitments, the ones the same every month whether or not you thought
    about them.

    Counted in months even when the summary is grouped by week. "Comes round
    every month" is what the word means here, and a fixed commitment does not
    become a different thing because you asked to see your spending seven days
    at a time.
    """
    by_pair: dict[tuple[str, object], set[str]] = defaultdict(set)
    for row in rows:
        pair = (row.description.casefold(), row.amount)
        by_pair[pair].add(f"{row.made.year:04d}-{row.made.month:02d}")
    return {desc for (desc, _), months in by_pair.items() if len(months) >= rules.recurring_months}
