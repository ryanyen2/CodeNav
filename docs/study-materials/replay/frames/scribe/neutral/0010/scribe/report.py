"""The note written beside each converted document.

Converting a PDF's text is lossy in ways the Markdown cannot show. Lines are
removed, hyphens are dropped, footnotes are moved to the end. All of that is
decided by rules that are right most of the time, and the output of a rule that
was wrong looks exactly like the output of a rule that was right. The report is
where those decisions are listed, so they can be checked against the source
without reading the two documents side by side.

It is deliberately short, and it deliberately has no timestamp: a report that
changes on every run is noise in a diff, and the useful question about a
conversion is whether it changed, not when it was made.

The file is named `<stem>.report.md` rather than `report.md`, because a document
called report.txt converts to report.md and a report called report.md would
overwrite it.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import fields
from pathlib import Path

from . import __version__
from .config import DEFAULTS, Settings
from .convert import Converted

# Long lists are the ones nobody reads. Past this many the rest is counted.
LIMIT = 12


def name_for(markdown: Path) -> Path:
    """Where the report for a converted document goes.

    The stem is kept whole rather than passed through `with_suffix`, so that
    `survey.2026.md` reports to `survey.2026.report.md` and not to
    `survey.report.md`, which is where `survey.txt` would report too.
    """
    return markdown.with_name(f"{markdown.stem}.report.md")


def _changed(settings: Settings) -> list[str]:
    """Every setting that differs from the default, as "section.key: a -> b"."""
    out: list[str] = []
    for section in fields(Settings):
        if section.name in {"source", "applied"}:
            continue
        current = getattr(settings, section.name)
        default = getattr(DEFAULTS, section.name)
        if current == default:
            continue
        for setting in fields(current):
            was, now = getattr(default, setting.name), getattr(current, setting.name)
            if was != now:
                out.append(f"`{section.name}.{setting.name}`: {was!r} to {now!r}")
    return out


def _tally(items: list[str]) -> list[str]:
    """Repeated strings as "the line (x3)", in the order they first appeared."""
    counts = Counter(items)
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    lines = [f"- `{item}`" + (f" ({counts[item]} times)" if counts[item] > 1 else "")
             for item in seen[:LIMIT]]
    if len(seen) > LIMIT:
        lines.append(f"- and {len(seen) - LIMIT} more")
    return lines


def render(result: Converted, source: Path, markdown: Path) -> str:
    """The report for one conversion."""
    settings = result.settings
    out: list[str] = [
        f"# What the conversion did to {source.name}",
        "",
        f"`{source.name}` became `{markdown.name}`, using scribe {__version__}.",
        "",
        f"{result.pages} pages in. Out came {result.headings} headings, "
        f"{result.paragraphs} paragraphs, {result.bullets} bullets and "
        f"{result.notes} footnotes.",
        "",
        "## Settings",
        "",
    ]

    if settings.source is None:
        out.append(
            f"No `scribe.toml` was found, so every rule ran on its default. "
            f"Adding one beside this document is what changes that."
        )
    else:
        applied = (
            ", ".join(f"`{pattern}`" for pattern in settings.applied)
            if settings.applied
            else "no document section"
        )
        out.append(f"From `{settings.source}`, matching {applied}.")

    changes = _changed(settings)
    if changes:
        out += ["", "Different from the defaults:", ""]
        out += [f"- {change}" for change in changes]

    if result.removed:
        out += [
            "",
            "## Lines removed as page furniture",
            "",
            f"{len(result.removed)} lines, being a running header, footer or page "
            "number repeated across the document. A section title that repeats on "
            "every page is removed by the same rule, so this list is worth reading:",
            "",
        ]
        out += _tally(result.removed)

    if result.joined:
        out += [
            "",
            "## Words rejoined across a line break",
            "",
            "A hyphen the typesetter added is dropped; one belonging to the word is "
            "kept. Nothing in the text says which is which, so these are guesses:",
            "",
        ]
        out += _tally(result.joined)

    if result.note_numbers:
        out += [
            "",
            "## Footnotes moved to the end",
            "",
            "Taken from the foot of the page they sat on and collected as Markdown "
            "footnotes: " + ", ".join(f"`[^{n}]`" for n in result.note_numbers) + ".",
        ]

    return "\n".join(out).strip() + "\n"
