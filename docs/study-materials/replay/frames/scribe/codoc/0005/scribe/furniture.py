"""Page furniture: the lines that belong to the paper, not to the document.

A running header, a running footer, a page number. They are on every page of a
scanned report and in none of the prose, and left in they land in the middle of
paragraphs.

Two policies live here. What they are tuned by is in scribe/config.py, under
[furniture]; the reasoning for the defaults is below, next to the rule each one
governs.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .config import DEFAULTS, FurnitureSettings
from .lines import Document, Line

SETTINGS = DEFAULTS.furniture

PAGE_NUMBER = re.compile(r"^\s*(?:[-–—]\s*)?(?:page\s+)?(\d{1,4}|[ivxlcdm]{1,7})\s*(?:[-–—])?\s*$", re.I)


def _normalise(text: str) -> str:
    """A key for comparing two lines that are the same header.

    Digits are folded, so "Chapter 3 — page 7" and "Chapter 3 — page 8" count as
    the same running header rather than as two different lines that each appear
    once.
    """
    return re.sub(r"\d+", "#", text.strip().casefold())


def find_repeated(doc: Document, settings: FurnitureSettings = SETTINGS) -> set[str]:
    """The normalised text of every line that repeats near an edge."""
    if len(doc.pages) < settings.min_pages:
        # Under `min_pages` there is nothing to establish a pattern with, and a
        # two page letter whose first line happens to echo its last would lose
        # both. A document this short is assumed to have no furniture.
        return set()

    seen: Counter[str] = Counter()
    for page in doc.pages:
        pages_own: set[str] = set()
        for line in page.lines:
            if line.is_blank:
                continue
            if not _near_edge(line, settings):
                continue
            pages_own.add(_normalise(line.text))
        seen.update(pages_own)

    # Two pages out of three is a running header; two out of forty is a
    # coincidence. The floor of 2 stops a share this small from matching a line
    # that appears once.
    threshold = max(2, int(len(doc.pages) * settings.repeat_share))
    return {key for key, count in seen.items() if count >= threshold}


def _near_edge(line: Line, settings: FurnitureSettings = SETTINGS) -> bool:
    """Whether a line sits in the margin, where furniture is.

    A page has to be long enough for "near the edge" to mean anything. On a page
    of four lines every line is in the margin, so ordinary body text that differs
    only by a number — "body 1", "body 2" — reads as a running header and is
    dropped. Real pages are long; short ones show up in tests and title pages.
    """
    if line.total_on_page < settings.min_page_lines:
        return False
    return line.from_top <= settings.edge or line.from_bottom <= settings.edge


def is_page_number(line: Line, settings: FurnitureSettings = SETTINGS) -> bool:
    """A line that is nothing but a number, near an edge."""
    if line.is_blank:
        return False
    if not _near_edge(line, settings):
        return False
    return bool(PAGE_NUMBER.match(line.text))


@dataclass
class Stripped:
    """What a strip did: the document, and the lines taken out of it."""

    document: Document
    removed: list[Line] = field(default_factory=list)


def partition(doc: Document, settings: FurnitureSettings = SETTINGS) -> Stripped:
    """Remove furniture from every page, keeping what was removed.

    Runs before heading detection, and that order is load bearing: a running
    header is often the section title, so it looks exactly like a heading. Taking
    the furniture out first means the heading rule never sees it. It also means a
    genuine heading that happens to repeat is gone before anything can rescue it,
    which is a real cost and the reason the threshold is as high as it is. It is
    also why what was removed is handed back rather than dropped on the floor:
    the conversion report lists it, so a heading eaten this way can be seen.
    """
    repeated = find_repeated(doc, settings)
    removed: list[Line] = []
    for page in doc.pages:
        kept = []
        for line in page.lines:
            if not line.is_blank:
                if _normalise(line.text) in repeated:
                    removed.append(line)
                    continue
                if settings.drop_page_numbers and is_page_number(line, settings):
                    removed.append(line)
                    continue
            kept.append(line)
        page.lines = kept
    return Stripped(document=doc, removed=removed)


def strip(doc: Document, settings: FurnitureSettings = SETTINGS) -> Document:
    """Remove furniture from every page. See `partition` for the reasoning."""
    return partition(doc, settings).document
