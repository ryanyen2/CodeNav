"""Putting the rules together, in the order they have to run in.

The order is not arbitrary and is the thing most likely to surprise somebody
changing this. Signs are normalised first, because every rule below reads them.
Transfers are found before duplicates are dropped, because a transfer is two rows
that look exactly like a duplicate. Categories are assigned before refunds are
netted, because a refund nets against the category it came from.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from . import categories, dedupe, money, months, recurring
from .rows import Row, read


@dataclass
class Month:
    name: str
    by_category: dict[str, Decimal] = field(default_factory=dict)
    transactions: int = 0

    @property
    def total(self) -> Decimal:
        return sum(self.by_category.values(), Decimal("0"))


@dataclass
class Summary:
    months: list[Month] = field(default_factory=list)
    recurring: set[str] = field(default_factory=set)
    rows_read: int = 0
    duplicates: int = 0
    transfers: int = 0
    uncategorised: int = 0

    def line(self) -> str:
        return (
            f"{self.rows_read} rows, {len(self.months)} months, "
            f"{self.duplicates} duplicates, {self.transfers} transfers, "
            f"{self.uncategorised} uncategorised, {len(self.recurring)} recurring"
        )

    def text(self) -> str:
        out: list[str] = []
        for month in self.months:
            out.append(f"## {month.name}")
            out.append("")
            for name in sorted(month.by_category, key=lambda k: month.by_category[k]):
                out.append(f"  {name:<16} {money.round_total(month.by_category[name]):>10}")
            out.append("")
            out.append(f"  {'total':<16} {money.round_total(month.total):>10}")
            out.append("")
        if self.recurring:
            out.append("## Recurring")
            out.append("")
            for name in sorted(self.recurring):
                out.append(f"  {name}")
            out.append("")
        return "\n".join(out).rstrip() + "\n"


def summarise(raw: str) -> Summary:
    rows: list[Row] = read(raw)
    result = Summary(rows_read=len(rows))
    if not rows:
        return result

    money.sign_convention(rows)

    kept, dropped = dedupe.drop_duplicates(rows)
    result.duplicates = dropped

    spending = [row for row in kept if not dedupe.is_transfer(row)]
    result.transfers = len(kept) - len(spending)

    result.recurring = recurring.find(spending)

    buckets: dict[str, dict[str, list[Decimal]]] = defaultdict(lambda: defaultdict(list))
    counts: dict[str, int] = defaultdict(int)
    for row in spending:
        name = categories.categorise(row)
        if name == categories.UNCATEGORISED:
            result.uncategorised += 1
        buckets[months.month_of(row)][name].append(row.amount)
        counts[months.month_of(row)] += 1

    for name in sorted(buckets):
        month = Month(name=name, transactions=counts[name])
        for category, amounts in buckets[name].items():
            month.by_category[category] = months.net(amounts)
        result.months.append(month)
    return result
