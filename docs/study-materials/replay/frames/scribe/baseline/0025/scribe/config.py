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
from typing import Any, get_type_hints

CONFIG_NAME = "scribe.toml"


class ConfigError(Exception):
    """A config file that cannot be used. The message names the key at fault."""


# ── the settings each rule reads ─────────────────────────────────────────────

@dataclass(frozen=True)
class FurnitureSettings:
    """See scribe/furniture.py for what these cost when they are wrong."""

    # How near the top or bottom a line has to be before it is considered at all.
    edge: int = 2
    # How much of the document a line has to appear on to count as repeated.
    # Two pages in five is a running header that started after a title page or
    # stopped before the appendices; two in forty is a coincidence. See
    # `min_repeats` below, which this is combined with.
    repeat_share: float = 0.4
    # Fewest pages a line must appear on, whatever the share works out at. Two is
    # the least that can mean anything: a line on one page has not repeated.
    # Raise it for a corpus where you want more evidence before dropping a line.
    min_repeats: int = 2
    # Fewest pages before furniture detection runs at all. Under this there is no
    # pattern to establish, and a short document is assumed to have none. Not to
    # be confused with `min_repeats`: this one is about the document, that one is
    # about the line.
    min_pages: int = 3
    # Whether a line that is nothing but a number near an edge is dropped.
    drop_page_numbers: bool = True

    def __post_init__(self) -> None:
        # Checked here rather than in the config reader so the invariant holds
        # however these are built. A threshold under two would make every line
        # near an edge furniture, which empties a document rather than tidying it.
        if self.min_repeats < 2:
            raise ConfigError(
                f"min_repeats must be at least 2, got {self.min_repeats}: a line on "
                "one page has not repeated, and a threshold below two would make "
                "every line near an edge furniture"
            )
        if not 0.0 <= self.repeat_share <= 1.0:
            raise ConfigError(
                f"repeat_share must be between 0 and 1, got {self.repeat_share}: it "
                "is a share of the pages, not a count of them"
            )

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


# ── reading the file ─────────────────────────────────────────────────────────

def _coerce(value: Any, hint: Any, where: str) -> Any:
    """One TOML value into one field, or a message saying why not.

    Types are checked rather than coerced. A config file is written by hand, so
    the useful thing is to name the mistake, not to guess around it. Note that
    `bool` is a subclass of `int` in Python, which is why the numeric branches
    rule it out: `edge = true` is a mistake, not the number one.
    """
    if hint is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{where}: expected true or false, got {value!r}")
        return value
    if hint is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{where}: expected a whole number, got {value!r}")
        return value
    if hint is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{where}: expected a number, got {value!r}")
        return float(value)
    if hint == frozenset[str]:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"{where}: expected a list of strings, got {value!r}")
        return frozenset(value)
    if hint == tuple[tuple[str, str], ...]:
        if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
            raise ConfigError(f"{where}: expected a table of string = string, got {value!r}")
        return tuple(value.items())
    raise ConfigError(f"{where}: no rule for reading this setting")


def _apply(current: Any, table: dict[str, Any], where: str) -> Any:
    """A TOML table onto a settings object, returning a new one."""
    # get_type_hints rather than field.type, because `from __future__ import
    # annotations` leaves the latter as strings.
    hints = get_type_hints(type(current))
    settable = {f.name for f in fields(current)}
    changes: dict[str, Any] = {}
    for key, value in table.items():
        if key not in settable:
            known = ", ".join(sorted(settable))
            raise ConfigError(f"{where}: unknown setting '{key}'. Known settings: {known}")
        changes[key] = _coerce(value, hints[key], f"{where}.{key}")
    try:
        return replace(current, **changes)
    except ConfigError as exc:
        # Raised by a settings class checking itself. It knows what is wrong but
        # not which file said so, which is the half worth having.
        raise ConfigError(f"{where}: {exc}") from exc


def _sections(settings: Settings, table: dict[str, Any], where: str) -> Settings:
    """Every rule section of one table onto a Settings, returning a new one."""
    changes: dict[str, Any] = {}
    for name, value in table.items():
        if name == "document":
            continue
        if name not in _SECTIONS:
            known = ", ".join(sorted(_SECTIONS))
            raise ConfigError(f"{where}: unknown section '[{name}]'. Known sections: {known}")
        if not isinstance(value, dict):
            raise ConfigError(f"{where}: '{name}' should be a table, written [{name}]")
        changes[name] = _apply(getattr(settings, name), value, f"{where} [{name}]")
    return replace(settings, **changes)


@dataclass(frozen=True)
class Config:
    """A parsed config file: the defaults, and the per-document overrides.

    Kept as the raw override tables rather than as finished Settings because
    which of them apply is not known until a document is named.
    """

    defaults: Settings = field(default_factory=Settings)
    documents: tuple[tuple[str, dict[str, Any]], ...] = ()
    path: Path | None = None

    def for_document(self, name: str) -> Settings:
        """The settings for one document, by file name.

        Patterns are matched against the file name alone, so a config file can be
        carried around with the corpus it describes. Order in the file is the
        order they are applied in, so the last matching section wins.
        """
        settings = replace(self.defaults, source=self.path)
        applied: list[str] = []
        for pattern, table in self.documents:
            if pattern != name and not fnmatch.fnmatch(name, pattern):
                continue
            where = f"{self.path or CONFIG_NAME} [document.\"{pattern}\"]"
            settings = _sections(settings, table, where)
            applied.append(pattern)
        return replace(settings, applied=tuple(applied))


def parse(raw: str, path: Path | None = None) -> Config:
    """Config text into a Config. Raises ConfigError with the key at fault."""
    try:
        table = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path or CONFIG_NAME}: {exc}") from exc

    where = str(path or CONFIG_NAME)
    defaults = _sections(Settings(source=path), table, where)

    documents: list[tuple[str, dict[str, Any]]] = []
    per_document = table.get("document", {})
    if not isinstance(per_document, dict):
        raise ConfigError(f"{where}: 'document' should be a table of file names")
    for pattern, overrides in per_document.items():
        if not isinstance(overrides, dict):
            raise ConfigError(
                f"{where}: [document.\"{pattern}\"] should contain rule sections, "
                f"such as [document.\"{pattern}\".text]"
            )
        # Validated now rather than when a document happens to match it, so a
        # typo in a section nobody exercises is still an error on the first run.
        _sections(defaults, overrides, f"{where} [document.\"{pattern}\"]")
        documents.append((pattern, overrides))

    return Config(defaults=defaults, documents=tuple(documents), path=path)


def load(path: Path) -> Config:
    return parse(path.read_text(encoding="utf-8"), path=path)


def find(start: Path) -> Path | None:
    """The nearest scribe.toml at or above a file or directory, if there is one.

    Searching upwards means a corpus in a subdirectory is covered by one config
    at the root of it, which is how these end up being kept.
    """
    here = start if start.is_dir() else start.parent
    for directory in [here, *here.parents]:
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def discover(start: Path) -> Config:
    """The config governing a path: the nearest file, or the defaults."""
    found = find(start)
    return load(found) if found else Config()
