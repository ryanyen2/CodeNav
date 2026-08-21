"""Putting the rules together, in the order they have to run in.

The order is not arbitrary and is the thing most likely to surprise somebody
changing this. Furniture is stripped before anything looks for headings, because
a running header is usually the section title and would otherwise be promoted on
every page. Footnote markers are rewritten while paragraphs are reflowed, in the
one pass over the prose, because both of them work on the same line of text and
walking the document twice for them was work with nothing to show for it.
Characters are normalised last, so every rule above sees the text as it came out
of the PDF rather than a partly rewritten version of it.

Every rule is handed the settings it needs. They are read once, from scribe.toml,
by whoever called this, so the same document can be converted twice with
different values and neither run touches the other.
"""
from __future__ import annotations

from dataclasses import dataclass

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

    def summary(self) -> str:
        return (
            f"{self.pages} pages, {self.headings} headings, "
            f"{self.paragraphs} paragraphs, {self.bullets} bullets, "
            f"{self.notes} notes, {self.dropped_furniture} lines of furniture"
        )


def _flatten(doc: Document) -> list[str]:
    """Every page's lines, one list, with the page boundaries kept as blanks."""
    out: list[str] = []
    for page in doc.pages:
        for line in page.lines:
            out.append(line.text)
        # A page boundary is a paragraph boundary unless the sentence runs on,
        # which reflow decides for itself. The blank line is what gives it the
        # chance to.
        out.append("")
    return out


def convert(raw: str, settings: Settings = DEFAULTS) -> Converted:
    doc = read(raw)
    before = len(doc.lines)
    furniture.strip(doc, settings)
    dropped = before - len(doc.lines)

    lines = blocks.collapse_blanks(_flatten(doc))

    result = Converted(markdown="", pages=len(doc.pages), dropped_furniture=dropped)

    # Headings and bullets are decided line by line, because both are properties
    # of a single line. Everything between them is prose, and prose is reflowed in
    # runs so a paragraph broken across a heading is not silently joined.
    out: list[str] = []
    run: list[str] = []

    def flush_run() -> None:
        for para in paragraphs.reflow(run, settings):
            result.notes += len(notes.MARKER.findall(para))
            out.append(notes.mark(para))
            result.paragraphs += 1
        run.clear()

    for position, line in enumerate(lines):
        following = lines[position + 1] if position + 1 < len(lines) else None
        level = blocks.heading_level(line, following)
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

    joined = _join(out)
    result.markdown = text.normalise(joined).strip() + "\n"
    return result


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
