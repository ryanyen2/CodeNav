"""Transactions that appear twice, and transactions that only look like it.

Two policies live here, and they are the ones most likely to catch somebody out.
The wording that marks a transfer, and what counts as the same transaction twice,
are in rules.toml.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .rows import Row
from .settings import Settings


@dataclass
class Merge:
    """Two rows treated as one transaction, and the one that was dropped.

    Kept so the summary can show them. Under "any wording" a merge can be wrong —
    two different things bought for the same amount on the same day — and a
    person who is told which rows were merged can see that at a glance, where a
    count alone would only say that something happened.
    """

    day: date
    kept: Row
    dropped: Row

    @property
    def reworded(self) -> bool:
        """Whether the wording differed, i.e. whether "same wording" would have
        kept both rows. These are the merges worth showing."""
        return self.kept.description.casefold() != self.dropped.description.casefold()


def key(row: Row, dated: str, match: str) -> tuple:
    """What makes two rows the same transaction.

    The date and the amount always. The description as well under "same
    wording", which is the safe answer: two rows for the same amount on the same
    day are more often two things than one thing written twice.

    Under "any wording" the description is ignored, for banks that word the same
    purchase differently between the pending row and the settled one. That merges
    a genuine second purchase of the same amount on the same day, which is the
    price of it, and why the summary lists what it merged.

    The bank's own reference would settle it exactly, but half of the exports do
    not carry one and the ones that do reuse them across statements, so it cannot
    be relied on alone.
    """
    day = getattr(row, dated)
    if match == "any wording":
        return (day, row.amount)
    return (day, row.amount, row.description.casefold())


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


def drop_duplicates(rows: list[Row], settings: Settings, by: str) -> tuple[list[Row], list[Merge]]:
    """Remove repeats, keeping the first of each.

    `by` is which summary this is for, because both halves of the question are
    answered per summary in rules.toml: what makes two rows the same, and which
    of the two dates counts as "the same day". A weekly summary lined up on the
    posting date should merge the rows the bank posted together, not the ones
    made together.

    Transfers are exempt, and that exemption is the whole reason this function is
    not three lines. A transfer between two of your own accounts exports as two
    rows on the same day for the same amount with the same wording — which is
    exactly the shape of a duplicate. Dropping one leg would leave a lone entry
    that looks like a real payment.
    """
    dated, match = settings.period_dates[by], settings.duplicates[by]
    seen: dict[tuple, Row] = {}
    kept: list[Row] = []
    merges: list[Merge] = []
    for row in rows:
        if is_transfer(row, settings):
            kept.append(row)
            continue
        this = key(row, dated, match)
        if this in seen:
            merges.append(Merge(day=getattr(row, dated), kept=seen[this], dropped=row))
            continue
        seen[this] = row
        kept.append(row)
    return kept, merges
