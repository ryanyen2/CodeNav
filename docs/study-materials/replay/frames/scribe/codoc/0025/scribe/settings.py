"""Every number a rule used to hard-code, in one place, loadable from a file.

The rules used to read module constants directly. That was fine while there was
one document, and wrong as soon as there were two: a handbook whose numbered
lists run long needs a different heading cut-off from a report whose headings are
terse, and there was nowhere to say so short of editing the source.

So the constants moved here into `Settings`, and every rule takes one as an
argument. Nothing reads a module constant any more. The defaults are the values
the rules used to hard-code, so a caller that passes nothing gets exactly the old
behaviour.

The file is TOML, read with `tomllib` from the standard library. A section per
rule module, and an optional `[documents."name.txt"]` block that overrides the
sections above it for one document only:

    [furniture]
    repeat_share = 0.6

    [documents."handbook.txt".blocks]
    max_heading_words = 16

An unknown section or key is an error rather than a shrug. A misspelled key that
silently does nothing is the worst way for a config file to fail, because the
output looks like the rule is broken.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

CONFIG_NAME = "scribe.toml"

# Prefixes that keep their hyphen when a line break falls inside them. Without a
# list like this "well-being" split across lines comes back as "wellbeing", and
# nothing in the text says which was meant. Lives here rather than in
# paragraphs.py because it is a setting a document can override; the reasoning
# behind it is in `paragraphs.dehyphenate`.
KEEP_HYPHEN = (
    "co", "e", "ex", "non", "post", "pre", "re", "self", "semi", "sub", "un", "well",
)


@dataclass(frozen=True)
class Settings:
    """What the rules are allowed to disagree about, per document."""

    # ── furniture ────────────────────────────────────────────────────────────
    # How near the top or bottom a line has to be before it is even considered.
    edge: int = 2
    # How many pages it takes to establish a pattern. Under this, a document is
    # assumed to have no furniture at all.
    min_pages: int = 3
    # How much of the document a line has to appear on to count as repeated.
    # Half rather than more, because a header is often missing from the title
    # page and from any page a full-width table or figure took over, and a five
    # page report carrying one on only two pages is common enough to be the
    # default's problem rather than the reader's.
    repeat_share: float = 0.5
    # The floor under that share, in pages. Whichever of the two is larger wins.
    # It exists because the share alone drops below two on a short document, and
    # a threshold of one would make every line near an edge furniture.
    min_repeats: int = 2

    # ── paragraphs ───────────────────────────────────────────────────────────
    keep_hyphen: frozenset[str] = frozenset(KEEP_HYPHEN)
    # Keep every hyphen, not only the listed prefixes. What you want for
    # technical writing full of real compounds, where dropping the hyphen does
    # more damage than keeping a typeset one.
    keep_all_hyphens: bool = False
    # A line at or under this length that ends a sentence ends the paragraph.
    short_line: int = 60

    # ── blocks ───────────────────────────────────────────────────────────────
    # Above this many words a numbered line is a list item, not a heading.
    max_heading_words: int = 12

    # ── notes ────────────────────────────────────────────────────────────────
    # How near the foot of a page a numbered line has to be to be a footnote.
    note_depth: int = 6

    # ── text ─────────────────────────────────────────────────────────────────
    # Fold ligatures and smart punctuation. A corpus being archived for fidelity
    # wants this off.
    normalise_characters: bool = True

    @property
    def min_page_lines(self) -> int:
        """A page shorter than this has no middle, so nothing is "near an edge".

        On a page of four lines with an edge of two, every line is in the margin,
        and body text differing only by a number reads as a running header.
        """
        return self.edge * 2 + 2


DEFAULTS = Settings()

# (section, key) -> (field on Settings, how to read it). Explicit so that an
# unknown key can be named in the error rather than ignored.
_FIELDS: dict[tuple[str, str], tuple[str, str]] = {
    ("furniture", "edge"): ("edge", "int"),
    ("furniture", "min_pages"): ("min_pages", "int"),
    ("furniture", "repeat_share"): ("repeat_share", "share"),
    ("furniture", "min_repeats"): ("min_repeats", "repeats"),
    ("paragraphs", "keep_hyphen"): ("keep_hyphen", "words"),
    ("paragraphs", "keep_all_hyphens"): ("keep_all_hyphens", "bool"),
    ("paragraphs", "short_line"): ("short_line", "int"),
    ("blocks", "max_heading_words"): ("max_heading_words", "int"),
    ("notes", "depth"): ("note_depth", "int"),
    ("text", "normalise"): ("normalise_characters", "bool"),
}

_SECTIONS = {section for section, _ in _FIELDS}
_DOCUMENTS = "documents"


class ConfigError(Exception):
    """The config file says something that cannot be acted on."""


def _read_value(kind: str, value: object, where: str) -> object:
    if kind == "int":
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConfigError(f"{where}: expected a number that is not negative, got {value!r}")
        return value
    if kind == "share":
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value <= 1:
            raise ConfigError(f"{where}: expected a share above 0 and at most 1, got {value!r}")
        return float(value)
    if kind == "bool":
        if not isinstance(value, bool):
            raise ConfigError(f"{where}: expected true or false, got {value!r}")
        return value
    if kind == "words":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ConfigError(f"{where}: expected a list of words, got {value!r}")
        return frozenset(word.strip().casefold() for word in value if word.strip())
    raise AssertionError(kind)


def _apply(base: Settings, tables: dict, where: str) -> Settings:
    """Fold one document's worth of TOML sections onto a Settings."""
    changes: dict[str, object] = {}
    for section, table in tables.items():
        if section == _DOCUMENTS:
            continue
        if section not in _SECTIONS:
            known = ", ".join(sorted(_SECTIONS))
            raise ConfigError(f"{where}: unknown section [{section}], expected one of {known}")
        if not isinstance(table, dict):
            raise ConfigError(f"{where}: [{section}] should be a section, got {table!r}")
        for key, value in table.items():
            field = _FIELDS.get((section, key))
            if field is None:
                known = ", ".join(sorted(k for s, k in _FIELDS if s == section))
                raise ConfigError(
                    f"{where}: unknown key {key!r} in [{section}], expected one of {known}"
                )
            name, kind = field
            changes[name] = _read_value(kind, value, f"{where}: {section}.{key}")
    return replace(base, **changes) if changes else base


@dataclass(frozen=True)
class Config:
    """A loaded config file: the defaults, plus any per-document overrides."""

    defaults: Settings = DEFAULTS
    overrides: dict[str, dict] = field(default_factory=dict)
    path: Path | None = None

    def for_document(self, name: str) -> Settings:
        """Settings for one document, by file name.

        A document with no block of its own gets the defaults, which is the
        common case and why the block is optional.
        """
        table = self.overrides.get(name)
        if table is None:
            return self.defaults
        return _apply(self.defaults, table, f"{self.path or CONFIG_NAME} [documents.{name!r}]")

    def names(self) -> list[str]:
        return sorted(self.overrides)


def load(path: Path) -> Config:
    """Read a config file. Raises ConfigError if it says something impossible."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: not valid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: cannot be read: {exc}") from exc

    defaults = _apply(DEFAULTS, raw, str(path))

    overrides = raw.get(_DOCUMENTS, {})
    if not isinstance(overrides, dict):
        raise ConfigError(f"{path}: [documents] should be a section, got {overrides!r}")
    for name, table in overrides.items():
        if not isinstance(table, dict):
            raise ConfigError(f"{path}: [documents.{name!r}] should be a section")
        # Validate now rather than at conversion time, so a typo in a document
        # nobody converted today is still an error today.
        _apply(defaults, table, f"{path} [documents.{name!r}]")

    return Config(defaults=defaults, overrides=overrides, path=path)


def find(start: Path) -> Config:
    """The config file beside a document, or the defaults if there is none.

    Only the document's own directory is looked in. Walking up the tree would
    mean a file three directories away could change the output of a conversion,
    which is hard to notice and harder to explain.
    """
    directory = start if start.is_dir() else start.parent
    candidate = directory / CONFIG_NAME
    return load(candidate) if candidate.is_file() else Config()
