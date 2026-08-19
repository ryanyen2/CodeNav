"""Putting broken lines back into paragraphs.

A PDF has no paragraphs. It has lines, broken wherever the typesetter's measure
ran out, and words broken wherever the hyphenation dictionary said they could be.
Reassembling them is most of what this program does.

Two policies live here.
"""
from __future__ import annotations

import re

from .settings import DEFAULTS, Settings

# The prefixes that keep their hyphen, and the length below which a line ending a
# sentence ends the paragraph, are settings now: see `scribe/settings.py`.

HYPHEN_END = re.compile(r"(\w+)-$")
SENTENCE_END = re.compile(r"[.!?][\"')\]]?$")


def dehyphenate(
    first: str, second: str, settings: Settings = DEFAULTS
) -> tuple[str, str] | None:
    """Join a word split across two lines, or decline to.

    Returns the rewritten pair, or None when the split should stand.

    The hyphen is dropped by default, because in a justified column most of them
    were put there by the typesetter and are not part of the word. The exceptions
    are `settings.keep_hyphen`, where a hyphen usually is part of the word. The
    alternative — keeping every hyphen — is defensible for a corpus of technical
    writing full of real compounds, and is `settings.keep_all_hyphens`.
    """
    match = HYPHEN_END.search(first)
    if not match:
        return None
    tail = second.lstrip()
    if not tail or not tail[0].isalpha():
        # "see figure 3-" followed by "1" is a number, not a broken word.
        return None
    stem = match.group(1)
    if settings.keep_all_hyphens or stem.casefold() in settings.keep_hyphen:
        joined = f"{stem}-{tail.split(' ', 1)[0]}"
    else:
        joined = f"{stem}{tail.split(' ', 1)[0]}"
    rest = tail.split(" ", 1)[1] if " " in tail else ""
    return first[: match.start()] + joined, rest


def is_break(previous: str, nxt: str, settings: Settings = DEFAULTS) -> bool:
    """Whether the newline between two lines ends the paragraph.

    A single newline continues the paragraph: that is the whole point of
    reflowing, and it is right far more often than not for body text. A blank
    line always breaks. The alternative — every newline is a break — is what you
    want for poetry or an address block, and would ruin a report.

    The one extra rule: a short line that ends a sentence also breaks. Without it
    the last line of every paragraph glues onto the first line of the next, which
    is the single most visible failure in the output.
    """
    if not previous.strip() or not nxt.strip():
        return True
    ended = previous.strip()
    if SENTENCE_END.search(ended) and len(ended) < settings.short_line:
        return True
    return False


def reflow(lines: list[str], settings: Settings = DEFAULTS) -> list[str]:
    """Lines into paragraphs, one paragraph per returned string."""
    out: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            out.append(" ".join(part for part in current if part))
            current.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            flush()
            index += 1
            continue

        joined = (
            dehyphenate(line, lines[index + 1], settings)
            if index + 1 < len(lines)
            else None
        )
        if joined is not None:
            head, remainder = joined
            current.append(head)
            # The remainder of the next line goes back on the queue, so the rest
            # of it is still subject to every rule below.
            lines = lines[: index + 1] + [remainder] + lines[index + 2 :]
            index += 1
            continue

        current.append(line.strip())
        if index + 1 < len(lines) and is_break(line, lines[index + 1], settings):
            flush()
        index += 1

    flush()
    return out
