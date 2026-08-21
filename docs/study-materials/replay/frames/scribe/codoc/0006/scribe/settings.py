"""The values the rules use, gathered in one place.

Every rule used to read a module constant of its own, so converting one awkward
document differently meant editing the source. These are the same values, in a
shape that can come out of a file instead, and every default here is the number
or the list that rule was already using.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

# Words that keep their hyphen when a line break falls inside them. Without a
# list like this "well-being" split across lines comes back as "wellbeing", and
# there is no way to tell from the text alone which of those was meant. It is the
# list paragraphs.py had before this file existed, unchanged.
KEEP_HYPHEN: tuple[str, ...] = (
    "co", "e", "ex", "non", "post", "pre", "re", "self", "semi", "sub", "un", "well",
)


@dataclass(frozen=True)
class Settings:
    """One document's settings. Every default is the behaviour before this existed."""

    # How much of the document a line has to repeat on before it counts as page
    # furniture. Two pages out of three is a running header, two out of forty is a
    # coincidence, and 0.6 is the share this has always used.
    repeat_share: float = 0.6
    # How near the top or bottom of a page a line has to be to be considered at all.
    edge: int = 2
    keep_hyphen: tuple[str, ...] = KEEP_HYPHEN

    def merged(self, **overrides) -> "Settings":
        """This settings object with some values replaced, ignoring empty ones."""
        clean = {k: v for k, v in overrides.items() if v is not None}
        if "keep_hyphen" in clean:
            clean["keep_hyphen"] = tuple(clean["keep_hyphen"])
        return replace(self, **clean) if clean else self


DEFAULTS = Settings()
