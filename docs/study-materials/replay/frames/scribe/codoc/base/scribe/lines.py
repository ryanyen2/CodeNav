"""Reading extracted text into pages and lines.

`pdftotext` and everything like it emits one long string with a form feed between
pages. That form feed is the only structure we get for free, and most of the
rules downstream need to know which page a line came from, so it is turned into
real objects here rather than being split on repeatedly.

Trailing whitespace is stripped and nothing else is touched. Every other
judgement belongs to a policy module, where it can be found and argued with.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PAGE_BREAK = "\f"


@dataclass
class Line:
    text: str
    page: int
    # Where it sat on its page, counted from the top. The furniture rules need
    # this: a line repeated at position 0 of every page is a running header, and
    # the same words in the middle of one page are just words.
    index: int
    total_on_page: int

    @property
    def is_blank(self) -> bool:
        return not self.text.strip()

    @property
    def from_top(self) -> int:
        return self.index

    @property
    def from_bottom(self) -> int:
        return self.total_on_page - 1 - self.index


@dataclass
class Page:
    number: int
    lines: list[Line] = field(default_factory=list)


@dataclass
class Document:
    pages: list[Page] = field(default_factory=list)

    @property
    def lines(self) -> list[Line]:
        return [line for page in self.pages for line in page.lines]


def read(raw: str) -> Document:
    """Extracted text into a Document.

    A page with nothing on it is kept rather than dropped. A blank page is a fact
    about the source, and the numbering has to keep matching the PDF a reader
    would open beside this.
    """
    doc = Document()
    for number, chunk in enumerate(raw.split(PAGE_BREAK), start=1):
        raw_lines = chunk.split("\n")
        # A page break usually follows a newline, so the last line of a page is
        # empty. Dropping it here keeps every page's line count honest, which the
        # from_bottom rules depend on.
        if raw_lines and raw_lines[-1] == "":
            raw_lines.pop()
        total = len(raw_lines)
        page = Page(number=number)
        for index, text in enumerate(raw_lines):
            page.lines.append(
                Line(text=text.rstrip(), page=number, index=index, total_on_page=total)
            )
        doc.pages.append(page)
    return doc
