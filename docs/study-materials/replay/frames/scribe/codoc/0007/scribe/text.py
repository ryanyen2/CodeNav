"""Characters that came from typesetting rather than from the writer.

One policy lives here. What it is tuned by is in scribe/config.py, under [text].
"""
from __future__ import annotations

from .config import DEFAULTS, TextSettings

SETTINGS = DEFAULTS.text

# Ligatures and smart punctuation are normalised to their plain equivalents,
# because the output is Markdown that somebody will grep, diff and paste into
# other things, and none of those handle "ﬁ" the way they handle "fi". A corpus
# being archived for fidelity would want the opposite, and sets normalise = false.
SUBSTITUTIONS = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "ft", "ﬆ": "st",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "--", "…": "...",
    "\u00a0": " ", "\u200b": "",
}


def normalise(text: str, settings: TextSettings = SETTINGS) -> str:
    """Fold the substitutions above, plus any the config file adds.

    A document with its own `extra` table is served last, so a corpus can add a
    character this table does not know about, or override one it does.
    """
    if not settings.normalise:
        return text
    for source, target in {**SUBSTITUTIONS, **dict(settings.extra)}.items():
        text = text.replace(source, target)
    return text
