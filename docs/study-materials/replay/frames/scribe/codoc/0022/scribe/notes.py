"""Footnotes.

In extracted text a footnote is two things far apart: a small number stuck to a
word in the prose, and the note itself at the foot of the page. Nothing connects
them except the number.

One policy lives here.
"""
from __future__ import annotations

import re

from .settings import DEFAULTS, Settings

# A note at the foot of a page: a number, then the text. The number is usually
# alone on the line's left edge because it was superscript in the original.
NOTE_LINE = re.compile(r"^\s*(\d{1,3})[\s.)]\s*(\S.*)$")

# A marker in the prose: digits welded to the end of a word, with no space. A
# space would make it an ordinary number in a sentence.
#
# The character before is captured rather than looked behind, and deliberately
# excludes digits: "comparable.1" is a marker after a sentence, and "0.8" is a
# number. Allowing any full stop made every decimal in the document a footnote
# reference, which is the kind of thing that reads fine until you read the output.
MARKER = re.compile(r"([a-z\)\]\"',;]\.?)(\d{1,3})(?![\d\w])")


def looks_like_note(
    text: str, from_bottom: int, settings: Settings = DEFAULTS
) -> bool:
    """Whether a line at the foot of a page is a note rather than prose.

    Position is what separates them. The same shape in the middle of a page is a
    numbered list item, and treating those as notes would move half a list to the
    end of the document.
    """
    return from_bottom <= settings.note_depth and bool(NOTE_LINE.match(text))


def split_note(text: str) -> tuple[str, str] | None:
    match = NOTE_LINE.match(text)
    return (match.group(1), match.group(2).strip()) if match else None


def mark(text: str) -> str:
    """Rewrite footnote markers in prose as Markdown references.

    Collected at the end rather than left inline, because that is what Markdown
    footnotes are and because a number welded to a word reads as part of the
    word. The alternative — leaving them where they were — keeps the page's
    original look and is what you would want if the notes were asides meant to be
    read in place.
    """
    return MARKER.sub(lambda m: f"{m.group(1)}[^{m.group(2)}]", text)
