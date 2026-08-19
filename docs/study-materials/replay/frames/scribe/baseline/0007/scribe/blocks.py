"""Which lines are headings, which are bullets, and how much space to keep.

Everything here is a guess made from the text alone. The extracted stream has no
font size, no weight and no indentation you can trust, so a heading has to be
recognised by how it is written.

Three policies live here. What they are tuned by is in scribe/config.py, under
[blocks]; the reasoning for the defaults is below, next to the rule each one
governs.
"""
from __future__ import annotations

import re

from .config import BlockSettings, DEFAULTS

SETTINGS = DEFAULTS.blocks

# A heading is recognised by its leading number: "3.", "3.1", "3.1.4 Findings".
# The alternative is to guess from length — a short line with no full stop — and
# that was tried first. It promoted every one-line answer, every table caption and
# every name in a list of names. Numbering is narrower and wrong far less often,
# at the cost of missing headings in documents that do not number them.
NUMBERED = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+(\S.*)$")

# Only these three. A line beginning with a real "•" is a bullet in any document
# that has bullets; "*" and "-" are common enough in prose that they are only
# taken as bullets when a space follows, which the pattern requires.
BULLET = re.compile(r"^\s*([-*•])\s+(\S.*)$")


def heading_level(
    text: str, following: str | None = None, settings: BlockSettings = SETTINGS
) -> tuple[int, str] | None:
    """The depth and the title, or None when this is not a heading.

    Depth comes from the numbering, so "3.1.4" is a third-level heading. Markdown
    is offset by one, because the document title takes `#`.

    `following` is the next line, and it is what separates a heading from the
    first line of a wrapped numbered list item. A heading has space under it; a
    list item runs straight on, usually indented. Without this, "1. Entering
    water above the knee, whether or not you are wearing a" was a heading and the
    rest of the sentence was a paragraph beneath it.
    """
    match = NUMBERED.match(text)
    if not match:
        return None
    number, title = match.groups()
    if len(title.split()) > settings.max_heading_words:
        # A numbered list item can look exactly like a heading. Length is the
        # only thing separating "3. Findings" from "3. We then asked each
        # participant to describe what they had understood, in their own words."
        return None
    if title.rstrip().endswith((".", ",", ";", ":")):
        return None
    if following is not None and following.strip():
        # Something on the very next line. A heading is followed by space, so
        # this is a wrapped list item unless the next line is itself a heading.
        if NUMBERED.match(following) is None and BULLET.match(following) is None:
            return None
    return number.count(".") + 2, title.strip()


def bullet(text: str) -> str | None:
    """The content of a bullet, or None."""
    match = BULLET.match(text)
    return match.group(2).strip() if match else None


def collapse_blanks(lines: list[str]) -> list[str]:
    """Runs of blank lines become one.

    A PDF is full of vertical space that was typesetting rather than meaning: the
    gap above a heading, the space left by a floated figure, the bottom of a
    short page. Keeping it would put four blank lines in the middle of a section
    for no reason a reader could see. The cost is that deliberate spacing, in a
    poem or a title page, is flattened too.
    """
    out: list[str] = []
    for line in lines:
        if not line.strip() and out and not out[-1].strip():
            continue
        out.append(line)
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out
