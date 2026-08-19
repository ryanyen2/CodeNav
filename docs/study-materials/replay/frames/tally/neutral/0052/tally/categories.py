"""Putting a transaction in a category.

One policy lives here, and it now lives in rules.toml: the merchant patterns,
their order, and the name an unmatched transaction gets. What is left here is
only the matching itself.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from .rows import Row
from .settings import Settings


class Unmatched(Exception):
    """Merchants no rule matches, when rules.toml says to stop for them.

    Every one of them is listed, not just the first. A person adding rules wants
    the whole list in front of them; finding them one run at a time is the kind
    of thing that gets abandoned halfway, with the rest of the statement still
    filed under a name nobody reads.
    """

    def __init__(self, rows: list[Row], source: Path) -> None:
        self.rows = rows
        seen = Counter(row.description for row in rows)
        first = {}
        for row in rows:
            first.setdefault(row.description, row.line)
        listed = "\n".join(
            f"  {name}" + (f"  ({count} rows, first at line {first[name]})" if count > 1
                           else f"  (line {first[name]})")
            for name, count in seen.most_common()
        )
        super().__init__(
            f"no rule matches:\n{listed}\n"
            f"add a rule for each in {source}, or set [categories] unmatched = \"bucket\" "
            "to file them under one name instead."
        )


def categorise(row: Row, settings: Settings) -> str:
    """The category, or whatever rules.toml calls uncategorised.

    The first matching rule wins, in the order the file has them, which is why
    the settings keep that order. See rules.toml for why the specific patterns
    sit above the general ones.

    Anything unmatched goes to a bucket rather than stopping the run. A summary
    with an uncategorised line is useful; a summary that refused to be written is
    not, and the bucket is visible in the output so it cannot be ignored.
    """
    for pattern, name in settings.categories:
        if pattern.search(row.description):
            return name
    return settings.uncategorised
