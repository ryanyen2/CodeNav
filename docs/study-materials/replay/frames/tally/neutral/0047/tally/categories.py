"""Putting a transaction in a category.

One policy lives here. The patterns it runs on are not here: they live in
`rules.toml`, so that adding a merchant is an edit to a list rather than a
change to a program. What is left in this file is the decision about what to do
with them.
"""
from __future__ import annotations

from .rows import Row
from .rules import Rules

UNCATEGORISED = "uncategorised"


class Unmatched(Exception):
    """Merchants that no rule covers, when the rules say to stop for them.

    Carries every one of them rather than the first. Stopping on the first would
    turn adding a month of new shops into one run per shop, and the whole point
    of stopping is to be told what to go and write down.
    """

    def __init__(self, descriptions: list[str]) -> None:
        self.descriptions = descriptions
        shops = "\n".join(f"  {name}" for name in descriptions)
        super().__init__(
            f"{len(descriptions)} merchant{'' if len(descriptions) == 1 else 's'} "
            f"match no rule:\n{shops}\n"
            f"Add them to rules.toml, or set [categories] unmatched = \"bucket\" "
            f"to file them under {UNCATEGORISED!r} instead."
        )


def categorise(row: Row, rules: Rules) -> str:
    """The category, or `uncategorised`.

    The FIRST rule that matches wins, which is a property of the order they are
    written in `rules.toml` and is documented there. Anything unmatched goes to
    a bucket rather than stopping the run: a summary with an uncategorised line
    is useful; a summary that refused to be written is not, and the bucket is
    visible in the output so it cannot be ignored.
    """
    for pattern, name in rules.categories:
        if pattern.search(row.description):
            return name
    return UNCATEGORISED
