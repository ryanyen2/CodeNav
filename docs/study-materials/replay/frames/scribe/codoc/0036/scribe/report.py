"""A short note, beside the Markdown, saying what the conversion did.

The Markdown is the output. This is the receipt: what was thrown away, what was
moved, and which settings were in force when it happened. It exists because the
lossy steps are invisible in the result — a running header that was removed
leaves no trace, and the only way to see it went is to be told.

Only what is unusual is spelled out. Settings at their defaults are summarised in
a line rather than tabulated, so that the settings a document actually changed
are the ones a reader's eye lands on.

There is deliberately no timestamp. The report is a function of the input, the
settings and nothing else, so converting twice gives the same file and a report
checked into git only changes when the conversion does.
"""
from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from .convert import Converted
from .settings import DEFAULTS, Config, Settings

# Past this many distinct furniture lines the list stops being worth reading.
MOST_FURNITURE = 10


def _changed(settings: Settings) -> list[tuple[str, str, str]]:
    """The settings that differ from the defaults, as (name, value, default)."""
    out = []
    for spec in fields(settings):
        value = getattr(settings, spec.name)
        default = getattr(DEFAULTS, spec.name)
        if value != default:
            out.append((spec.name, _show(value), _show(default)))
    return out


def _show(value: object) -> str:
    if isinstance(value, bool):
        # Spelled as the config file spells it, so the report can be copied back.
        return "true" if value else "false"
    if isinstance(value, frozenset):
        return ", ".join(sorted(value)) or "(none)"
    return str(value)


def _furniture(result: Converted) -> list[str]:
    lines = []
    ranked = sorted(result.furniture_lines.items(), key=lambda kv: (-kv[1], kv[0]))
    for text, count in ranked[:MOST_FURNITURE]:
        shown = text.strip()
        times = f" ({count} times)" if count > 1 else ""
        lines.append(f"- `{shown}`{times}")
    extra = len(ranked) - MOST_FURNITURE
    if extra > 0:
        lines.append(f"- and {extra} more")
    return lines


def _notes(result: Converted) -> list[str]:
    """The notes, numbered by where they sit in the finished document.

    Two numbers, because they are not always the same one. The ordinal is the
    position at the foot of the Markdown; the marker is what the document itself
    called the note, and a report numbering its notes from one on every page
    yields several called `[^1]`. The ordinal finds it, the marker confirms it.
    """
    lines = []
    for position, (number, body) in enumerate(result.note_list, start=1):
        lines.append(f"{position}. `[^{number}]` {_excerpt(body)}")
    return lines


def _excerpt(body: str) -> str:
    """Enough of a note to recognise it by, and no more."""
    body = " ".join(body.split())
    if len(body) <= NOTE_EXCERPT:
        return body
    return body[:NOTE_EXCERPT].rsplit(" ", 1)[0] + "..."


def render(
    result: Converted, source: Path, target: Path, config: Config | None = None
) -> str:
    """The report for one conversion, as Markdown."""
    out: list[str] = [
        f"# {source.name}",
        "",
        f"Converted to `{target.name}`.",
        "",
        f"{result.summary()}.",
    ]

    if result.furniture_lines:
        out += [
            "",
            "## Removed",
            "",
            f"{result.dropped_furniture} lines were taken to be page furniture "
            "and are not in the Markdown:",
            "",
            *_furniture(result),
        ]

    if result.note_list:
        out += [
            "",
            "## Moved",
            "",
            f"{result.notes} footnotes were lifted off the foot of the page they "
            "were on and collected at the end of the document, in this order. "
            "The marker beside each is the one to search for in the Markdown.",
            "",
            *_notes(result),
        ]

    out += ["", "## Settings", ""]
    source_of = "the built-in defaults"
    if config is not None and config.path is not None:
        source_of = f"`{config.path}`"
    changed = _changed(result.settings)
    if changed:
        out += [f"From {source_of}. These differ from the defaults:", ""]
        out += [
            f"- `{name}`: {value} (default {default})"
            for name, value, default in changed
        ]
    else:
        out.append(f"From {source_of}. Every setting is at its default.")

    return "\n".join(out).strip() + "\n"
