"""Putting the rules together, in the order they have to run in.

The order is not arbitrary and is the thing most likely to surprise somebody
changing this. Furniture is stripped before anything looks for headings, because
a running header is usually the section title and would otherwise be promoted on
every page. Footnotes are collected before paragraphs are reflowed, because a
note at the foot of a page would otherwise be glued to the last sentence above
it. Characters are normalised last, so every rule above sees the text as it came
out of the PDF rather than a partly rewritten version of it.

Every rule takes its numbers from a `Settings`, which is threaded through from
here. Nothing below reads a module constant, so converting the same text twice
with different settings gives two different answers and no state is left behind.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from . import blocks, furniture, notes, paragraphs, text
from .lines import Document, read
from .settings import DEFAULTS, Settings


@dataclass
class Converted:
    markdown: str
    headings: int = 0
    paragraphs: int = 0
    bullets: int = 0
    notes: int = 0
    dropped_furniture: int = 0
    pages: int = 0
    # What the rules were told, and what they threw away. Both are here for the
    # conversion report: the counts say how much happened, and the furniture is
    # the one thing a reader may want back, so it is named rather than totalled.
    settings: Settings = DEFAULTS
    furniture_lines: Counter[str] = field(default_factory=Counter)

    def summary(self) -> str:
        return (
            f"{self.pages} pages, {self.headings} headings, "
            f"{self.paragraphs} paragraphs, {self.bullets} bullets, "
            f"{self.notes} notes, {self.dropped_furniture} lines of furniture"
        )


@dataclass
class _Collected:
    body: list[str] = field(default_factory=list)
    footnotes: list[tuple[str, str]] = field(default_factory=list)


def _collect_notes(
    doc: Document, out: _Collected, settings: Settings = DEFAULTS
) -> list[str]:
    """Pull the notes off the foot of each page, return what is left."""
    remaining: list[str] = []
    for page in doc.pages:
        for line in page.lines:
            if notes.looks_like_note(line.text, line.from_bottom, settings):
                split = notes.split_note(line.text)
                if split:
                    out.footnotes.append(split)
                    continue
            remaining.append(line.text)
        # A page boundary is a paragraph boundary unless the sentence runs on,
        # which reflow decides for itself. The blank line is what gives it the
        # chance to.
        remaining.append("")
    return remaining


def _join(parts: list[str]) -> str:
    """Blocks into a document, with one blank line between them.

    Consecutive bullets are the exception: a blank line between them makes a
    loose list, which Markdown renders with paragraph spacing inside what was
    meant to be a tight list.
    """
    out: list[str] = []
    for part in parts:
        if out and part.startswith("- ") and out[-1].startswith("- "):
            out.append(part)
            continue
        if out:
            out.append("")
        out.append(part)
    return "\n".join(out)


def convert(raw: str, settings: Settings = DEFAULTS) -> Converted:
    doc = read(raw)
    # Counted rather than subtracted, so the report can name the lines that went.
    # Blank lines are never furniture, so leaving them out of both sides is safe.
    before = Counter(line.text for line in doc.lines if line.text.strip())
    furniture.strip(doc, settings)
    after = Counter(line.text for line in doc.lines if line.text.strip())
    dropped = before - after

    collected = _Collected()
    lines = _collect_notes(doc, collected, settings)
    lines = blocks.collapse_blanks(lines)

    result = Converted(
        markdown="",
        pages=len(doc.pages),
        dropped_furniture=sum(dropped.values()),
        settings=settings,
        furniture_lines=dropped,
    )

    # Headings and bullets are decided line by line, because both are properties
    # of a single line. Everything between them is prose, and prose is reflowed in
    # runs so a paragraph broken across a heading is not silently joined.
    out: list[str] = []
    run: list[str] = []

    def flush_run() -> None:
        for para in paragraphs.reflow(run, settings):
            out.append(notes.mark(para))
            result.paragraphs += 1
        run.clear()

    for position, line in enumerate(lines):
        following = lines[position + 1] if position + 1 < len(lines) else None
        level = blocks.heading_level(line, following, settings)
        if level is not None:
            flush_run()
            depth, title = level
            out.append(f"{'#' * min(depth, 6)} {title}")
            result.headings += 1
            continue
        item = blocks.bullet(line)
        if item is not None:
            flush_run()
            out.append(f"- {notes.mark(item)}")
            result.bullets += 1
            continue
        run.append(line)
    flush_run()

    if collected.footnotes:
        for number, body in collected.footnotes:
            out.append(f"[^{number}]: {body}")
            result.notes += 1

    joined = _join(out)
    result.markdown = text.normalise(joined, settings).strip() + "\n"
    return result
