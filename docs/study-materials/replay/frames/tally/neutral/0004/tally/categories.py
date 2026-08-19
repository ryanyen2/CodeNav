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
