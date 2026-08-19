"""Putting broken lines back into paragraphs.

A PDF has no paragraphs. It has lines, broken wherever the typesetter's measure
ran out, and words broken wherever the hyphenation dictionary said they could be.
Reassembling them is most of what this program does.

Two policies live here. What they are tuned by is in scribe/config.py, under
[paragraphs]; the reasoning for the defaults is below, next to the rule each one
governs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import DEFAULTS, ParagraphSettings

SETTINGS = DEFAULTS.paragraphs

HYPHEN_END = re.compile(r"(\w+)-$")
SENTENCE_END = re.compile(r"[.!?][\"')\]]?$")


def dehyphenate(
    first: str, second: str, settings: ParagraphSettings = SETTINGS
) -> tuple[str, str] | None:
    """Join a word split across two lines, or decline to.

    Returns the rewritten pair, or None when the split should stand.

    Every hyphen is dropped unless something says otherwise, because in a
    justified column most of them were put there by the typesetter and are not
    part of the word. Two things say otherwise, and both are settings that are
    off until a corpus asks for them.

    `keep_hyphen` lists the prefixes where a hyphen usually is part of the word.
    It is empty by default, so "well-" and "being" rejoin as "wellbeing" until
    "well" is named in a config file. Nothing in the text says which was meant,
    and which compounds a document contains is a fact about that document rather
    than about this program.

    `keep_all_hyphens` keeps the lot, which is defensible for a corpus of
    technical writing full of real compounds and wrong for a justified column.

    The rejoined words are listed in the conversion report, which is the place to
    find out which prefixes your own corpus turns out to need.
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


def is_break(previous: str, nxt: str, settings: ParagraphSettings = SETTINGS) -> bool:
    """Whether the newline between two lines ends the paragraph.

    A single newline continues the paragraph: that is the whole point of
    reflowing, and it is right far more often than not for body text. A blank
    line always breaks. The alternative — every newline is a break — is what you
    want for poetry or an address block, and would ruin a report.

    The one extra rule: a line of at most `short_line` characters that ends a
    sentence also breaks. Without it the last line of every paragraph glues onto
    the first line of the next, which is the single most visible failure in the
    output.
    """
    if not previous.strip() or not nxt.strip():
        return True
    if SENTENCE_END.search(previous.strip()) and len(previous.strip()) < settings.short_line:
        return True
    return False


@dataclass
class Reflowed:
    """What a reflow did: the paragraphs, and the words it put back together."""

    paragraphs: list[str] = field(default_factory=list)
    joined: list[str] = field(default_factory=list)


def reflow_recording(lines: list[str], settings: ParagraphSettings = SETTINGS) -> Reflowed:
    """Lines into paragraphs, keeping the list of words rejoined.

    The rejoined words are the least reversible thing this module does — a
    dropped hyphen cannot be told from one that was never there — so they are
    handed back for the conversion report to list.
    """
    out: list[str] = []
    joined_words: list[str] = []
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
            dehyphenate(line, lines[index + 1], settings) if index + 1 < len(lines) else None
        )
        if joined is not None:
            head, remainder = joined
            current.append(head)
            joined_words.append(head.rsplit(" ", 1)[-1])
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
    return Reflowed(paragraphs=out, joined=joined_words)


def reflow(lines: list[str], settings: ParagraphSettings = SETTINGS) -> list[str]:
    """Lines into paragraphs, one paragraph per returned string."""
    return reflow_recording(lines, settings).paragraphs
