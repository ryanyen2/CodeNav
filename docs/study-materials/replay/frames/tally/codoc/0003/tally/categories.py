"""Putting a transaction in a category.

One policy lives here, and it now lives in rules.toml: the merchant patterns,
their order, and the name an unmatched transaction gets. What is left here is
only the matching itself.
"""
from __future__ import annotations

from .rows import Row
from .settings import Settings


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
