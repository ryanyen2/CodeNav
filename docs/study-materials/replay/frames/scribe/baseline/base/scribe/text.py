"""Characters that came from typesetting rather than from the writer.

One policy lives here.
"""
from __future__ import annotations

# Ligatures and smart punctuation are normalised to their plain equivalents,
# because the output is Markdown that somebody will grep, diff and paste into
# other things, and none of those handle "ﬁ" the way they handle "fi". A corpus
# being archived for fidelity would want the opposite, and would delete this.
SUBSTITUTIONS = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "ft", "ﬆ": "st",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "--", "…": "...",
    " ": " ", "​": "",
}


def normalise(text: str) -> str:
    for source, target in SUBSTITUTIONS.items():
        text = text.replace(source, target)
    return text
