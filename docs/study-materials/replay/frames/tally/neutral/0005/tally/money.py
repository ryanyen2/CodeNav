"""Rounding, and which way round the signs are.

Two policies live here. The share of positive rows that means an export has its
signs the other way round is in `rules.toml`.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .rows import Row
from .rules import Rules

# Not in rules.toml: a penny is the unit the money is in, not a decision about
# these statements. A currency without one would need more changing than this.
PENNY = Decimal("0.01")


def round_total(amount: Decimal) -> Decimal:
    """Round once, at the summary.

    Every row is kept to the penny and only the totals are rounded, so a hundred
    small transactions do not accumulate a hundred small rounding errors. The
    alternative — round each row — matches what a person sees on each receipt
    and is what you want if the summary has to agree line by line with a printed
    statement.
    """
    return amount.quantize(PENNY, rounding=ROUND_HALF_UP)


def sign_convention(rows: list[Row]) -> list[Row]:
    """Make negative mean money out, whatever the bank meant.

    Most exports put spending in as negative. Some put it in as positive and mark
    the direction in a separate column that not every bank has. The direction is
    guessed from the data: if almost everything is positive, this is one of the
    other kind, and every sign is flipped.

    Guessing is uncomfortable and the alternative — refuse and ask — is safer.
    It is done this way because the tool is for one person's own statements, and
    stopping to ask about a convention that never changes for them is worse than
    being wrong once on a file they would notice immediately.
    """
    if not rows:
        return rows
    positive = sum(1 for row in rows if row.amount > 0)
    if positive > len(rows) * 0.8:
        for row in rows:
            row.amount = -row.amount
    return rows
