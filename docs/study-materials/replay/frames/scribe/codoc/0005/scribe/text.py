"""Characters that came from typesetting rather than from the writer.

One policy lives here.
"""
from __future__ import annotations

from .settings import DEFAULTS, Settings

# Ligatures and smart punctuation are normalised to their plain equivalents,
# because the output is Markdown that somebody will grep, diff and paste into
# other things, and none of those handle "ﬁ" the way they handle "fi". A corpus
# being archived for fidelity wants the opposite, and turns `text.normalise` off
# in its config file rather than deleting this.
SUBSTITUTIONS = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "ft", "ﬆ": "st",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "--", "…": "...",
    " ": " ", "​": "",
}


def normalise(text: str, settings: Settings = DEFAULTS) -> str:
    if not settings.normalise_characters:
        return text
    for source, target in SUBSTITUTIONS.items():
        text = text.replace(source, target)
    return text
