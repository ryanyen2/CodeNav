"""Where the rules get their settings.

The rules used to read module constants, so changing one meant editing code and
there was no way to run the same statement through two different rule sets. The
values now come from rules.toml, are read once, and are handed to each rule as
an argument. No rule reaches for a global.

rules.toml is the only source of these values. Nothing here supplies a default
for a missing one; a file with a piece missing stops the run and says which
piece, rather than falling back to a copy in the code that nobody remembers is
there.

Reading is done once, at the edge — the CLI loads a Settings and passes it down.
Everything below takes it as an argument and never reads a file.
"""
from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

DEFAULT_RULES = Path(__file__).with_name("rules.toml")

# The merchant rules, compiled and in the order rules.toml has them. Order is the
# policy here — the first match wins — so a sequence, never a mapping.
Rules = tuple[tuple[re.Pattern[str], str], ...]

# The choices rules.toml gets to make. They are in code, not in the file, because
# each one is a behaviour that some module has to implement — the file picks
# between them, it cannot invent a new one. See rules.toml for what each means.
#
# This module imports nothing else from tally on purpose. Everything else may
# read the settings, so the settings may read nothing: that is what keeps the
# imports one-way and the policy in one place.
SHOW_TRANSFERS = ("apart", "never", "spending")
PERIOD_NAMES = ("month", "week")            # periods.PERIODS implements these
DATES = ("made", "posted")                  # the two dates a row carries
MATCH_ON = ("same wording", "any wording")  # what makes two rows one transaction
UNMATCHED = ("stop", "bucket")              # a merchant no rule matches


class RulesError(Exception):
    """rules.toml is missing, unreadable, or says something that cannot be used.

    Raised rather than repaired. A pattern that does not compile would otherwise
    become a rule that matches nothing, and a category quietly gone missing from
    a summary is worse than a run that stopped and said so.
    """


@dataclass(frozen=True)
class Settings:
    """Every value the rules need, read from one file.

    Frozen because a rule that could change the settings it was handed would put
    the policy back where it started: somewhere other than rules.toml.
    """

    categories: Rules
    uncategorised: str
    unmatched: str
    period_dates: Mapping[str, str]
    duplicates: Mapping[str, str]
    transfer_words: tuple[str, ...]
    show_transfers: str
    transfer_category: str
    recurring_months: int
    flip_when_positive_share_above: float
    source: Path = DEFAULT_RULES


def load(path: Path | str | None = None) -> Settings:
    """Read the rules. `path` is for tests and for trying an alternative set."""
    source = Path(path) if path is not None else DEFAULT_RULES
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise RulesError(f"cannot read the rules at {source}: {exc}") from exc
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RulesError(f"{source} is not valid TOML: {exc}") from exc

    return Settings(
        categories=_categories(raw, source),
        uncategorised=_text(raw, source, "categories", "uncategorised"),
        unmatched=_choice(raw, source, "categories", "unmatched", UNMATCHED),
        period_dates=_per_summary(raw, source, "periods", DATES),
        duplicates=_per_summary(raw, source, "duplicates", MATCH_ON),
        transfer_words=_words(raw, source),
        show_transfers=_choice(raw, source, "transfers", "show", SHOW_TRANSFERS),
        transfer_category=_text(raw, source, "transfers", "category"),
        recurring_months=_months(raw, source),
        flip_when_positive_share_above=_share(raw, source),
        source=source,
    )


def _table(raw: dict[str, Any], source: Path, name: str) -> dict[str, Any]:
    table = raw.get(name)
    if not isinstance(table, dict):
        raise RulesError(f"{source}: no [{name}] section")
    return table


def _text(raw: dict[str, Any], source: Path, table: str, key: str) -> str:
    value = _table(raw, source, table).get(key)
    if not isinstance(value, str) or not value.strip():
        raise RulesError(f"{source}: [{table}] {key} has to be a non-empty string")
    return value


def _categories(raw: dict[str, Any], source: Path) -> Rules:
    """The merchant rules, compiled, in the order they are written.

    Order is the policy — the first match wins — so it is kept exactly as the
    file has it and never sorted.
    """
    rules = _table(raw, source, "categories").get("rules")
    if not isinstance(rules, list) or not rules:
        raise RulesError(
            f"{source}: no [[categories.rules]]; every transaction would be uncategorised"
        )

    compiled: list[tuple[re.Pattern[str], str]] = []
    for number, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            raise RulesError(f"{source}: rule {number} is not a [[categories.rules]] table")
        pattern, category = rule.get("match"), rule.get("category")
        if not isinstance(pattern, str) or not pattern:
            raise RulesError(f"{source}: rule {number} needs a match")
        if not isinstance(category, str) or not category.strip():
            raise RulesError(f"{source}: rule {number} ({pattern}) needs a category")
        try:
            compiled.append((re.compile(pattern, re.I), category))
        except re.error as exc:
            raise RulesError(
                f"{source}: rule {number} ({category}) has a bad pattern: {exc}"
            ) from exc
    return tuple(compiled)


def _words(raw: dict[str, Any], source: Path) -> tuple[str, ...]:
    words = _table(raw, source, "transfers").get("words")
    if not isinstance(words, list) or not words:
        raise RulesError(f"{source}: [transfers] words has to be a non-empty list")
    if not all(isinstance(word, str) and word.strip() for word in words):
        raise RulesError(f"{source}: [transfers] words has to be a list of non-empty strings")
    return tuple(word.casefold() for word in words)


def _choice(raw: dict[str, Any], source: Path, table: str, key: str,
            choices: tuple[str, ...]) -> str:
    """One of a fixed set of behaviours.

    Checked against the ones that exist, because a misspelling would otherwise
    fall through to whatever the code happens to do last.
    """
    value = _table(raw, source, table).get(key)
    if value not in choices:
        raise RulesError(
            f"{source}: [{table}] {key} is {value!r}, "
            f"which has to be one of {', '.join(choices)}"
        )
    return value


def _per_summary(raw: dict[str, Any], source: Path,
                 table: str, choices: tuple[str, ...]) -> Mapping[str, str]:
    """A choice made separately for the monthly and the weekly summary.

    Both have to be named. Left to a default, the summary nobody wrote a line for
    would answer a different question from the one they did write, and the two
    files would disagree without saying why.
    """
    values = _table(raw, source, table)
    missing = [name for name in PERIOD_NAMES if name not in values]
    if missing:
        raise RulesError(f"{source}: [{table}] says nothing about {', '.join(missing)}")
    unknown = [name for name in values if name not in PERIOD_NAMES]
    if unknown:
        raise RulesError(
            f"{source}: [{table}] has no such summary: {', '.join(unknown)}; "
            f"there is {' and '.join(PERIOD_NAMES)}"
        )
    for name in PERIOD_NAMES:
        if values[name] not in choices:
            raise RulesError(
                f"{source}: [{table}] {name} is {values[name]!r}, "
                f"which has to be one of {', '.join(choices)}"
            )
    # A copy the rules cannot write back into, for the reason Settings is frozen.
    return MappingProxyType({name: values[name] for name in PERIOD_NAMES})


def _months(raw: dict[str, Any], source: Path) -> int:
    months = _table(raw, source, "recurring").get("months")
    # bool is an int in Python, and `months = true` is a mistake worth catching.
    if not isinstance(months, int) or isinstance(months, bool) or months < 1:
        raise RulesError(
            f"{source}: [recurring] months has to be a whole number of 1 or more"
        )
    return months


def _share(raw: dict[str, Any], source: Path) -> float:
    share = _table(raw, source, "money").get("flip_when_positive_share_above")
    if isinstance(share, bool) or not isinstance(share, (int, float)):
        raise RulesError(
            f"{source}: [money] flip_when_positive_share_above "
            "has to be a number between 0 and 1"
        )
    if not 0 < share <= 1:
        raise RulesError(
            f"{source}: [money] flip_when_positive_share_above is {share}, "
            "which has to be between 0 and 1"
        )
    return float(share)
