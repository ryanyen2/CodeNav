"""Putting a transaction in a category.

One policy lives here.
"""
from __future__ import annotations

import re

from .rows import Row

# Merchant patterns to categories, in order. The FIRST match wins, which is why
# the specific ones are above the general ones: "shell energy" is a utility and
# "shell" is fuel, and a list in the other order would put the electricity bill
# in the car.
#
# The alternative is to require exactly one match and refuse when two apply. That
# is stricter and it is what an accounting system should do; here it would stop
# the month's summary over one ambiguous coffee shop.
RULES: list[tuple[str, str]] = [
    (r"shell energy|british gas|octopus energy", "utilities"),
    (r"shell|bp\b|esso|texaco", "fuel"),
    (r"tesco|sainsbury|aldi|lidl|co-?op", "groceries"),
    (r"pret|greggs|costa|starbucks|caf[eé]", "eating out"),
    (r"tfl|trainline|uber|citymapper", "transport"),
    (r"netflix|spotify|patreon", "subscriptions"),
    (r"boots|pharmacy|nhs", "health"),
    (r"rent|landlord", "housing"),
    (r"salary|payroll", "income"),
]

COMPILED = [(re.compile(pattern, re.I), name) for pattern, name in RULES]

UNCATEGORISED = "uncategorised"


def categorise(row: Row) -> str:
    """The category, or `uncategorised`.

    Anything unmatched goes to a bucket rather than stopping the run. A summary
    with an uncategorised line is useful; a summary that refused to be written is
    not, and the bucket is visible in the output so it cannot be ignored.
    """
    for pattern, name in COMPILED:
        if pattern.search(row.description):
            return name
    return UNCATEGORISED
