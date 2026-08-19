"""Page furniture: the lines that belong to the paper, not to the document.

A running header, a running footer, a page number. They are on every page of a
scanned report and in none of the prose, and left in they land in the middle of
paragraphs.

Two policies live here.
"""
from __future__ import annotations

import re
from collections import Counter

from .lines import Document, Line

# How near the top or bottom a line has to be before it is even considered.
# Furniture sits in the margin; a sentence that happens to repeat does not.
EDGE = 2

# A page has to be long enough for "near the edge" to mean anything. On a page of
# four lines every line is in the margin, so ordinary body text that differs only
# by a number — "body 1", "body 2" — was being read as a running header and
# dropped. Real pages are long; short ones show up in tests and in title pages.
MIN_PAGE = EDGE * 2 + 2

# How much of the document a line has to appear on. Two pages out of three is a
# running header; two pages out of forty is a coincidence.
REPEAT_SHARE = 0.6

PAGE_NUMBER = re.compile(r"^\s*(?:[-–—]\s*)?(?:page\s+)?(\d{1,4}|[ivxlcdm]{1,7})\s*(?:[-–—])?\s*$", re.I)


def _normalise(text: str) -> str:
    """A key for comparing two lines that are the same header.

    Digits are folded, so "Chapter 3 — page 7" and "Chapter 3 — page 8" count as
    the same running header rather than as two different lines that each appear
    once.
    """
    return re.sub(r"\d+", "#", text.strip().casefold())


def find_repeated(doc: Document) -> set[str]:
    """The normalised text of every line that repeats near an edge."""
    if len(doc.pages) < 3:
        # Under three pages there is nothing to establish a pattern with, and a
        # two page letter whose first line happens to echo its last would lose
        # both. A document this short is assumed to have no furniture.
        return set()

    seen: Counter[str] = Counter()
    for page in doc.pages:
        pages_own: set[str] = set()
        for line in page.lines:
            if line.is_blank:
                continue
            if not _near_edge(line):
                continue
            pages_own.add(_normalise(line.text))
        seen.update(pages_own)

    threshold = max(2, int(len(doc.pages) * REPEAT_SHARE))
    return {key for key, count in seen.items() if count >= threshold}


def _near_edge(line: Line) -> bool:
    if line.total_on_page < MIN_PAGE:
        return False
    return line.from_top <= EDGE or line.from_bottom <= EDGE


def is_page_number(line: Line) -> bool:
    """A line that is nothing but a number, near an edge."""
    if line.is_blank:
        return False
    if not _near_edge(line):
        return False
    return bool(PAGE_NUMBER.match(line.text))


def strip(doc: Document) -> Document:
    """Remove furniture from every page.

    Runs before heading detection, and that order is load bearing: a running
    header is often the section title, so it looks exactly like a heading. Taking
    the furniture out first means the heading rule never sees it. It also means a
    genuine heading that happens to repeat is gone before anything can rescue it,
    which is a real cost and the reason the threshold is as high as it is.
    """
    repeated = find_repeated(doc)
    for page in doc.pages:
        kept = []
        for line in page.lines:
            if not line.is_blank:
                if _normalise(line.text) in repeated:
                    continue
                if is_page_number(line):
                    continue
            kept.append(line)
        page.lines = kept
    return doc
