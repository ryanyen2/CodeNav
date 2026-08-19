"""Which month a transaction belongs to, and what a refund does to it.

Two policies live here.
"""
from __future__ import annotations

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
