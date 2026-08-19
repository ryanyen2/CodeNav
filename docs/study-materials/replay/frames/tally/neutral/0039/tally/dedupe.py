"""Transactions that appear twice, and transactions that only look like it.

Two policies live here, and they are the ones most likely to catch somebody out.
The wording that marks a transfer is in rules.toml; the rest is here.
"""
from __future__ import annotations

from .rows import Row
from .settings import Settings

# A pair is a duplicate when the date, the amount and the description all match.
# The bank's own reference would be exact, but half of the exports do not carry
# one and the ones that do reuse them across statements, so it cannot be relied
# on alone.


def key(row: Row) -> tuple:
    return (row.made, row.amount, row.description.casefold())


def is_transfer(row: Row, settings: Settings) -> bool:
    """Money moved between the account holder's own accounts.

    Recognising one is all that happens here. What is then done with it — listed
    beside the total, left out, or counted as spending — is set in rules.toml and
    carried out in summary.py, because that is a question about the shape of the
    summary rather than about what this row is.

    The wording is the only signal available. An account column would be better,
    since a transfer is really a pair of rows in two accounts, but half of the
    exports have one account per file and no way to tell which.
    """
    text = row.description.casefold()
    return any(word in text for word in settings.transfer_words)


def drop_duplicates(rows: list[Row], settings: Settings) -> tuple[list[Row], int]:
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
        if is_transfer(row, settings):
            kept.append(row)
            continue
        if key(row) in seen:
            dropped += 1
            continue
        seen.add(key(row))
        kept.append(row)
    return kept, dropped
