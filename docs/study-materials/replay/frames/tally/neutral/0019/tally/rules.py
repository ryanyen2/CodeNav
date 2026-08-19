"""The settings the policies run on, and where they are read from.

Every judgement in this tool is made by a small function in one of the policy
modules, and every one of those functions used to reach for a constant defined
next to it. That worked, but it meant the inputs to a decision were invisible at
the call site and could not be varied without editing the module. They arrive as
an argument now, and this is what arrives.

The values live in `rules.toml`, beside this file, so they can be changed
without touching code. `--rules` on the command line points at a different file.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("rules.toml")

# What [transfers] handling accepts. Spelled out here so a typo is caught when
# the file is read, rather than quietly behaving like whichever branch the code
# happens to fall through to.
SHOW = "show"
HIDE = "hide"
HANDLING = (SHOW, HIDE)


class RulesError(Exception):
    """A rules file that cannot be used.

    Raised rather than falling back to anything built in. A summary produced
    against rules other than the ones in the file you are looking at is wrong in
    a way that is very hard to see, so the run stops and says which file and
    what about it.
    """


@dataclass(frozen=True)
class Rules:
    """What the policies need in order to decide anything.

    Frozen, because a rule reading these should not be able to change them for
    everything downstream of it. Patterns arrive compiled: they are applied once
    per row, and compiling them per row would be the most expensive thing here.
    """
    categories: tuple[tuple[re.Pattern[str], str], ...]
    transfer_words: tuple[str, ...]
    transfer_handling: str
    recurring_months: int
    positive_share: float

    @property
    def shows_transfers(self) -> bool:
        return self.transfer_handling == SHOW

    def category_names(self) -> list[str]:
        """Every category a rule can produce, in the order the rules are tried."""
        return [name for _, name in self.categories]


def _require(table: dict, key: str, kind: type, where: str, path: Path):
    """One setting, present and the right shape, or an error naming the file."""
    if key not in table:
        raise RulesError(f"{path}: [{where}] is missing {key!r}")
    value = table[key]
    if isinstance(value, bool):
        # bool is a subclass of int, so `months = true` would otherwise pass for
        # an integer and then mean 1.
        raise RulesError(f"{path}: [{where}] {key} should be {kind.__name__}, got a boolean")
    if kind is float and isinstance(value, int):
        value = float(value)                  # `positive_share = 1` is a number too
    if not isinstance(value, kind):
        raise RulesError(
            f"{path}: [{where}] {key} should be {kind.__name__}, got {type(value).__name__}"
        )
    return value


def parse(raw: str, path: Path = DEFAULT_PATH) -> Rules:
    """A rules file into Rules. Anything wrong with it is raised, not worked around."""
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise RulesError(f"{path}: not readable as TOML: {exc}") from exc

    entries = data.get("rule")
    if not entries:
        raise RulesError(f"{path}: no [[rule]] entries, so nothing could be categorised")

    categories: list[tuple[re.Pattern[str], str]] = []
    for position, entry in enumerate(entries, start=1):
        where = f"rule {position}"
        pattern = _require(entry, "pattern", str, where, path)
        name = _require(entry, "category", str, where, path)
        try:
            compiled = re.compile(pattern, re.I)
        except re.error as exc:
            raise RulesError(f"{path}: [{where}] {pattern!r} is not a regular expression: {exc}") from exc
        categories.append((compiled, name))

    words = _require(data.get("transfers", {}), "words", list, "transfers", path)
    if not all(isinstance(word, str) for word in words):
        raise RulesError(f"{path}: [transfers] words should all be strings")

    months = _require(data.get("recurring", {}), "months", int, "recurring", path)
    if months < 1:
        raise RulesError(f"{path}: [recurring] months should be at least 1, got {months}")

    share = _require(data.get("money", {}), "positive_share", float, "money", path)
    if not 0 < share < 1:
        raise RulesError(f"{path}: [money] positive_share should be between 0 and 1, got {share}")

    return Rules(
        categories=tuple(categories),
        transfer_words=tuple(word.casefold() for word in words),
        recurring_months=months,
        positive_share=share,
    )


def load(path: Path | str | None = None) -> Rules:
    """The rules in a file, or the ones that ship with tally."""
    path = DEFAULT_PATH if path is None else Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RulesError(f"{path}: cannot be read: {exc}") from exc
    return parse(raw, path)


@lru_cache(maxsize=1)
def default() -> Rules:
    """The rules that ship with tally, read once.

    Cached because `check` summarises a folder in one process and the file does
    not change underneath it. Anything wanting to pick up an edit mid-run should
    call `load`.
    """
    return load()
