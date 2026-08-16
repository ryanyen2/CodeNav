"""Transactions that appear twice, and transactions that only look like it.

Two policies live here, and they are the ones most likely to catch somebody out.
"""
from __future__ import annotations

from .rows import Row

# A pair is a duplicate when the date, the amount and the description all match.
# The bank's own reference would be exact, but half of the exports do not carry
# one and the ones that do reuse them across statements, so it cannot be relied
# on alone.
TRANSFER_WORDS = ("transfer", "to savings", "from savings", "own account", "xfer")


def key(row: Row) -> tuple:
    return (row.made, row.amount, row.description.casefold())


def is_transfer(row: Row) -> bool:
    """Money moved between the account holder's own accounts.

    Excluded from the summary, because nothing was spent: the money is still
    theirs. Including them would double every transfer, once as money out of one
    account and once as money in to another, and make a month of moving savings
    around look like a month of spending.
    """
    text = row.description.casefold()
    return any(word in text for word in TRANSFER_WORDS)


def drop_duplicates(rows: list[Row]) -> tuple[list[Row], int]:
    """Remove repeats, keeping the first of each.

    Transfers are exempt, and that exemption is the whole reason this function is
    not three lines. A transfer between two of your own accounts exports as two
    rows on the same day for the same amount with the same wording — which is
    exactly the shape of a duplicate. Dropping one leg would leave a lone entry
    that looks like a real payment.
    """
    seen: set[tuple] = set()
    kept: list[Row] = []
    dropped = 0
    for row in rows:
        if is_transfer(row):
            kept.append(row)
            continue
        if key(row) in seen:
            dropped += 1
            continue
        seen.add(key(row))
        kept.append(row)
    return kept, dropped
