"""Putting the rules together, in the order they have to run in.

The order is not arbitrary and is the thing most likely to surprise somebody
changing this. Signs are normalised first, because every rule below reads them.
Transfers are found before duplicates are dropped, because a transfer is two rows
that look exactly like a duplicate. Categories are assigned before refunds are
netted, because a refund nets against the category it came from.

Every rule is handed the settings it needs. They are read once, from rules.toml,
by whoever called this — the rules themselves hold no policy, so the same
statement can be run through a different rule set without touching code.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from . import categories, dedupe, money, periods, recurring
from .rows import Row, read
from .settings import Settings


@dataclass
class Period:
    """One month or one week of the summary, whichever it was grouped by."""

    name: str
    by_category: dict[str, Decimal] = field(default_factory=dict)
    transactions: int = 0
    # Money moved between the person's own accounts in this period. Deliberately
    # not part of `total`: it is theirs still, and under show = "apart" it is
    # reported beside the total rather than inside it.
    moved: Decimal = Decimal("0")

    @property
    def total(self) -> Decimal:
        return sum(self.by_category.values(), Decimal("0"))


@dataclass
class Summary:
    periods: list[Period] = field(default_factory=list)
    recurring: set[str] = field(default_factory=set)
    rows_read: int = 0
    duplicates: int = 0
    transfers: int = 0
    uncategorised: int = 0
    by: str = "month"        # what the periods above are: "month" or "week"
    transfer_label: str = "transfers"    # what rules.toml calls them

    def line(self) -> str:
        return (
            f"{self.rows_read} rows, {len(self.periods)} {self.by}s, "
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
            if period.moved:
                # Beneath the total and marked, rather than in it. The note is on
                # every period on purpose: one month gets read, or copied out on
                # its own, far more often than the whole file does.
                out.append(
                    f"  {self.transfer_label:<16} "
                    f"{money.round_total(period.moved):>10}   (not in the total)"
                )
            out.append("")
        if self.recurring:
            out.append("## Recurring")
            out.append("")
            for name in sorted(self.recurring):
                out.append(f"  {name}")
            out.append("")
        return "\n".join(out).rstrip() + "\n"


def summarise(raw: str, settings: Settings, by: str = "month") -> Summary:
    """A statement into a summary, grouped by month or by week.

    `by` changes one thing: which label a row is filed under. Every rule above it
    is the same either way, including recurring, which is counted in months
    whatever the grouping — see recurring.py for why.
    """
    label_of = periods.label_for(by)         # before the rows, so a bad name always says so
    rows: list[Row] = read(raw)
    result = Summary(rows_read=len(rows), by=by)
    if not rows:
        return result

    money.sign_convention(rows, settings)

    kept, dropped = dedupe.drop_duplicates(rows, settings)
    result.duplicates = dropped

    spending = [row for row in kept if not dedupe.is_transfer(row, settings)]
    result.transfers = len(kept) - len(spending)

    result.recurring = recurring.find(spending, settings)

    grouped: dict[str, dict[str, list[Decimal]]] = defaultdict(lambda: defaultdict(list))
    counts: dict[str, int] = defaultdict(int)
    for row in spending:
        name = categories.categorise(row, settings)
        if name == settings.uncategorised:
            result.uncategorised += 1
        label = label_of(row)
        grouped[label][name].append(row.amount)
        counts[label] += 1

    for name in sorted(grouped):
        period = Period(name=name, transactions=counts[name])
        for category, amounts in grouped[name].items():
            period.by_category[category] = periods.net(amounts)
        result.periods.append(period)
    return result
