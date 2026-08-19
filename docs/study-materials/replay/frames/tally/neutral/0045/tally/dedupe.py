"""Transactions that appear twice, and transactions that only look like it.

Two policies live here, and they are the ones most likely to catch somebody out.
The wording that marks a transfer is in `rules.toml`.
"""
from __future__ import annotations

from .rows import Row
from .rules import Rules


FIELDS = {
    "date": lambda row: row.made,
    "posted": lambda row: row.posted,
    "amount": lambda row: row.amount,
    "description": lambda row: row.description.casefold(),
}


def key(row: Row, rules: Rules) -> tuple:
    """What makes two rows the same row, per `[duplicates] match`.

    The bank's own reference would be exact, but half of the exports do not
    carry one and the ones that do reuse them across statements, so it cannot be
    relied on alone.

    Leaving `description` out of the match is what collapses one purchase
    exported under three spellings of the same shop. It also collapses two
    genuinely different purchases of the same amount on the same day, and there
    is no way to tell those apart from in here -- the row that gets dropped and
    the row that should have been are identical in every field being compared.
    Which is why the count of dropped rows is reported rather than kept quiet.
    """
    return tuple(FIELDS[field](row) for field in rules.duplicate_match)


def is_transfer(row: Row, rules: Rules) -> bool:
    """Money moved between the account holder's own accounts.

    Excluded from the summary, because nothing was spent: the money is still
    theirs. Including them would double every transfer, once as money out of one
    account and once as money in to another, and make a month of moving savings
    around look like a month of spending.
    """
    text = row.description.casefold()
    return any(word in text for word in rules.transfer_words)


def drop_duplicates(rows: list[Row], rules: Rules) -> tuple[list[Row], int]:
    """Remove repeats, keeping the first of each.

    Transfers are exempt, and that exemption is the whole reason this function is
    not three lines. A transfer between two of your own accounts exports as two
    rows on the same day for the same amount with the same wording -- which is
    exactly the shape of a duplicate. Dropping one leg would leave a lone entry
    that looks like a real payment.
    """
    seen: set[tuple] = set()
    kept: list[Row] = []
    dropped = 0
    for row in rows:
        if is_transfer(row, rules):
            kept.append(row)
            continue
        if key(row) in seen:
            dropped += 1
            continue
        seen.add(key(row))
        kept.append(row)
    return kept, dropped
