"""The values the rules use, gathered in one place.

Every rule used to read a module constant, which meant changing one for a single
awkward document meant editing the source. These are the same values, in a shape
that can come from a file instead.
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Settings:
    """One document's settings. Defaults match the behaviour before this existed."""

    repeat_share: float = 0.6
    edge: int = 2
    keep_hyphen: tuple[str, ...] = ("well", "self", "half", "non", "re", "co")

    def merged(self, **overrides) -> "Settings":
        """This settings object with some values replaced, ignoring empty ones."""
        clean = {k: v for k, v in overrides.items() if v is not None}
        return replace(self, **clean) if clean else self


DEFAULTS = Settings()
