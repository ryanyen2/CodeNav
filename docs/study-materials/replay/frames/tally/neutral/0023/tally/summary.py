"""Putting the rules together, in the order they have to run in.

The order is not arbitrary and is the thing most likely to surprise somebody
changing this. Signs are normalised first, because every rule below reads them.
Transfers are found before duplicates are dropped, because a transfer is two rows
that look exactly like a duplicate. Categories are assigned before refunds are
netted, because a refund nets against the category it came from.

This is also where the rules are handed to the policies that need them. Nothing
below reaches for a setting of its own; each is given what it needs, so the whole
of what a run depends on is a single object passed in at the top.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from . import categories, dedupe, money, periods, recurring
from .rows import Row, read
from .rules import Rules, default


@dataclass
class Period:
    """One month, or one week, of spending broken down by category.

    `moved` is money that went between the account holder's own accounts in this
    period. It is kept out of `by_category`, and so out of `total`, because it
    was not spent -- but it is counted and reported, because it left the account
    and somebody looking at the total is entitled to know where the rest went.
    """
    name: str
    by_category: dict[str, Decimal] = field(default_factory=dict)
    transactions: int = 0
    moved: Decimal = Decimal("0")
    moves: int = 0

    @property
    def total(self) -> Decimal:
        return sum(self.by_category.values(), Decimal("0"))


@dataclass
class Summary:
    periods: list[Period] = field(default_factory=list)
    recurring: set[str] = field(default_factory=set)
    period_kind: str = "month"
    rows_read: int = 0
    duplicates: int = 0
    transfers: int = 0
    uncategorised: int = 0

    def line(self) -> str:
        kind = self.period_kind if len(self.periods) == 1 else f"{self.period_kind}s"
        return (
            f"{self.rows_read} rows, {len(self.periods)} {kind}, "
            f"{self.duplicates} duplicates, {self.transfers} transfers, "
            f"{self.uncategorised} uncategorised, {len(self.recurring)} recurring"
        )

    def text(self) -> str:
        out: list[str] = []
        for period in self.periods:
            out.append(f"## {period.name}")
            out.append("")
            for name in sorted(period.by_category, key=lambda k: period.by_category[k]):
                out.append(f"  {name:<16} {money.round_total(period.by_category[name]):>10}")
            out.append("")
            out.append(f"  {'total':<16} {money.round_total(period.total):>10}")
            if period.moves:
                # The count is here on purpose. Transfers are exempt from the
                # duplicate rule, so a period holding two identical moves shows
                # twice the amount of one that holds a single move -- and without
                # the count beside it, that reads as an error in the total rather
                # than as two transfers, which is a question only the reader can
                # settle by looking at their statement.
                moves = "transfer" if period.moves == 1 else "transfers"
                out.append(
                    f"  {'moved':<16} {money.round_total(period.moved):>10}"
                    f"   ({period.moves} {moves}, not in the total)"
                )
            out.append("")
        if self.recurring:
            # These are monthly whichever way the spending above is grouped. Under
            # weekly sections that needs saying, or the heading looks like it is
            # claiming they come round every week. Under monthly ones it does not,
            # and saying it anyway would change a report nobody asked to change.
            out.append("## Recurring" if self.period_kind == "month" else "## Recurring monthly")
            out.append("")
            for name in sorted(self.recurring):
                out.append(f"  {name}")
            out.append("")
        return "\n".join(out).rstrip() + "\n"


def summarise(raw: str, rules: Rules | None = None, by: str = "month") -> Summary:
    """A statement into a summary, grouped by month or by week.

    `by` picks the grouping and nothing else: the same rows, the same rules and
    the same categories, cut a different way. Only the netting of refunds
    actually notices, because that happens within whatever a period turns out to
    be -- which `periods.net` says more about.
    """
    if by not in periods.OF:
        raise ValueError(f"cannot group by {by!r}, only by {' or '.join(sorted(periods.OF))}")
    period_of = periods.OF[by]
    rules = default() if rules is None else rules

    rows: list[Row] = read(raw)
    result = Summary(rows_read=len(rows), period_kind=by)
    if not rows:
        return result

    money.sign_convention(rows, rules)

    kept, dropped = dedupe.drop_duplicates(rows, rules)
    result.duplicates = dropped

    spending = [row for row in kept if not dedupe.is_transfer(row, rules)]
    result.transfers = len(kept) - len(spending)

    result.recurring = recurring.find(spending, rules)

    buckets: dict[str, dict[str, list[Decimal]]] = defaultdict(lambda: defaultdict(list))
    counts: dict[str, int] = defaultdict(int)
    for row in spending:
        name = categories.categorise(row, rules)
        if name == categories.UNCATEGORISED:
            result.uncategorised += 1
        buckets[period_of(row)][name].append(row.amount)
        counts[period_of(row)] += 1

    for name in sorted(buckets):
        period = Period(name=name, transactions=counts[name])
        for category, amounts in buckets[name].items():
            period.by_category[category] = periods.net(amounts)
        result.periods.append(period)
    return result
