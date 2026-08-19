"""The settings the rules read, and the file they can be changed in.

Every rule in this program makes a judgement, and every one of those judgements
is right for some documents and wrong for others. The values that decide them
used to be module constants, which meant a corpus with one awkward document had
to be converted by editing the source. They live here instead, as plain data the
rules are handed.

The shape is one dataclass per rule module, and `Settings` holding all of them.
Defaults are exactly the values the constants had, so a run with no config file
converts a document the same way it always did.

A `scribe.toml` looks like this:

    [furniture]
    repeat_share = 0.6

    [paragraphs]
    keep_all_hyphens = true

    # and, for one document only:
    [document."memo.txt".text]
    normalise = false

Tables at the top level are the defaults for every document. A `[document.NAME]`
table overrides them for the documents whose file name matches NAME, which may be
a glob. Where several patterns match one document they are applied in the order
they appear in the file, so the last one wins.

What is not settable: the regular expressions that recognise a heading, a bullet,
a note or a page number. They are structure rather than policy — the code reads
their capture groups by number — and a mistyped pattern in a config file would
fail somewhere far from the typo. Changing those is still a code change.
"""
from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

CONFIG_NAME = "scribe.toml"


class ConfigError(Exception):
    """A config file that cannot be used. The message names the key at fault."""


# ── the settings each rule reads ─────────────────────────────────────────────

@dataclass(frozen=True)
class FurnitureSettings:
    """See scribe/furniture.py for what these cost when they are wrong."""

    # How near the top or bottom a line has to be before it is considered at all.
    edge: int = 2
    # How much of the document a line has to appear on to count as repeated. Two
    # pages out of three is a running header; two out of forty is a coincidence.
    repeat_share: float = 0.6
    # Fewest pages before furniture detection runs. Under this there is no
    # pattern to establish, and a short document is assumed to have none.
    min_pages: int = 3
    # Whether a line that is nothing but a number near an edge is dropped.
    drop_page_numbers: bool = True

    @property
    def min_page_lines(self) -> int:
        """A page shorter than this has no margin worth the name.

        On a page of four lines every line is near an edge, so body text that
        differs only by a number reads as a running header and is dropped.
        """
        return self.edge * 2 + 2


@dataclass(frozen=True)
class ParagraphSettings:
    """See scribe/paragraphs.py."""

    # A line at most this long that ends a sentence also ends the paragraph.
    short_line: int = 60
    # Keep every hyphen at a line break rather than the listed prefixes only.
    # Defensible for technical writing, which is full of real compounds.
    keep_all_hyphens: bool = False
    # Prefixes whose hyphen survives a line break: "well-being", not "wellbeing".
    keep_hyphen: frozenset[str] = frozenset({
        "co", "e", "ex", "non", "post", "pre", "re", "self", "semi", "sub", "un", "well",
    })


@dataclass(frozen=True)
class BlockSettings:
    """See scribe/blocks.py."""

    # A numbered line longer than this is a list item, not a heading.
    max_heading_words: int = 12


@dataclass(frozen=True)
class NoteSettings:
    """See scribe/notes.py."""

    # Collect notes at the end as Markdown footnotes. Off leaves them where they
    # were, which is what you want if they are asides meant to be read in place.
    collect: bool = True
    # How near the foot of a page a numbered line has to be to be a note.
    foot_zone: int = 6


@dataclass(frozen=True)
class TextSettings:
    """See scribe/text.py."""

    # Fold ligatures and smart punctuation to plain equivalents. A corpus being
    # archived for fidelity wants this off.
    normalise: bool = True
    # Substitutions applied on top of the built-in table, as pairs so the
    # settings stay hashable.
    extra: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ReportSettings:
    """The note written beside each converted document."""

    write: bool = True


@dataclass(frozen=True)
class Settings:
    furniture: FurnitureSettings = field(default_factory=FurnitureSettings)
    paragraphs: ParagraphSettings = field(default_factory=ParagraphSettings)
    blocks: BlockSettings = field(default_factory=BlockSettings)
    notes: NoteSettings = field(default_factory=NoteSettings)
    text: TextSettings = field(default_factory=TextSettings)
    report: ReportSettings = field(default_factory=ReportSettings)

    # Where these came from, and which document sections were applied. Carried
    # so the conversion report can say what was in force; neither affects a
    # conversion.
    source: Path | None = None
    applied: tuple[str, ...] = ()


DEFAULTS = Settings()

_SECTIONS = {
    "furniture": FurnitureSettings,
    "paragraphs": ParagraphSettings,
    "blocks": BlockSettings,
    "notes": NoteSettings,
    "text": TextSettings,
    "report": ReportSettings,
}
