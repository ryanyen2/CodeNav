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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_RULES = Path(__file__).with_name("rules.toml")

# The merchant rules, compiled and in the order rules.toml has them. Order is the
# policy here — the first match wins — so a sequence, never a mapping.
Rules = tuple[tuple[re.Pattern[str], str], ...]

# What can be done with a transfer. In code rather than in rules.toml because
# each one is a behaviour that summary.py has to implement — the file chooses
# between them, it cannot invent a fourth. See rules.toml for what each means.
SHOW_TRANSFERS = ("apart", "never", "spending")


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
    transfer_words: tuple[str, ...]
    show_transfers: str
    transfer_category: str
    recurring_months: int
    flip_when_positive_share_above: float


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
        transfer_words=_words(raw, source),
        show_transfers=_show(raw, source),
        transfer_category=_text(raw, source, "transfers", "category"),
        recurring_months=_months(raw, source),
        flip_when_positive_share_above=_share(raw, source),
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


def _show(raw: dict[str, Any], source: Path) -> str:
    """What to do with transfers.

    Checked against the list of behaviours that exist, because a misspelling
    would otherwise fall through to whatever the code does last — and the wrong
    answer here either hides money or counts money that was never spent.
    """
    show = _table(raw, source, "transfers").get("show")
    if show not in SHOW_TRANSFERS:
        raise RulesError(
            f"{source}: [transfers] show is {show!r}, "
            f"which has to be one of {', '.join(SHOW_TRANSFERS)}"
        )
    return show


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
