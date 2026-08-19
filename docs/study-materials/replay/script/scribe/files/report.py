"""A short note beside the Markdown saying what the conversion did.

Written next to the output rather than printed, so it is still there when
somebody looks at the Markdown a week later and wonders where a line went.
"""
from __future__ import annotations

from pathlib import Path


def render(name: str, result, settings) -> str:
    """The report for one document."""
    lines = [
        f"# {name}",
        "",
        f"{result.pages} pages in, {result.paragraphs} paragraphs out.",
        "",
        "## What the rules did",
        "",
        f"- Removed {result.dropped_furniture} lines of page furniture, which is a "
        f"line repeated near the edge of at least "
        f"{settings.repeat_share:.0%} of the pages.",
        f"- Promoted {result.headings} numbered lines to headings.",
        f"- Kept {result.bullets} bullets.",
        f"- Gathered {result.notes} footnotes at the end.",
        "",
        "## Settings used",
        "",
        f"- repeat_share: {settings.repeat_share}",
        f"- edge: {settings.edge}",
        "",
    ]
    return "\n".join(lines)


def write_report(path: Path, name: str, result, settings) -> None:
    path.write_text(render(name, result, settings), encoding="utf-8")
