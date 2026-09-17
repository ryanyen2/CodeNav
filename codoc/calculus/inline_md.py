"""Inline Markdown ⇄ (plain text, marks by offset).

The three inline forms codoc's prose uses, and nothing else:

    **bold**                         → bold
    [label](https://host/path)       → link{href}
    [label](codoc:file.py#Symbol)    → cite{file, symbol}

Parsing is exact and total: anything that is not one of these three forms is
plain text, including a lone ``*`` or an unmatched bracket. Rendering is the
inverse on the same three forms. This module is used at the ingest boundary
(an LLM or a person handing in Markdown) and at export; the store itself never
holds Markdown.

Offsets are Unicode scalar offsets into the plain text, half-open ``[start, end)``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from codoc.calculus.text import BOLD, CITE, LINK

_TOKEN = re.compile(
    r"\*\*(?P<bold>[^*\n](?:[^*\n]|\*(?!\*))*?)\*\*"
    r"|\[(?P<label>[^\]\n]*)\]\((?P<target>codoc:[^)\s]+|https?://[^)\s]+)\)"
)


@dataclass(frozen=True)
class Span:
    kind: str
    attrs: dict
    start: int
    end: int   # exclusive


def parse(md: str) -> tuple[str, list[Span]]:
    """Plain text and its spans. Bold may not nest; a link's label is plain."""
    out: list[str] = []
    spans: list[Span] = []
    pos = 0
    length = 0
    for m in _TOKEN.finditer(md):
        out.append(md[pos:m.start()])
        length += len(md[pos:m.start()])
        if m.group("bold") is not None:
            inner = m.group("bold")
            spans.append(Span(BOLD, {}, length, length + len(inner)))
            out.append(inner)
            length += len(inner)
        else:
            label = m.group("label")
            target = m.group("target")
            if target.startswith("codoc:"):
                ref = target[len("codoc:"):]
                file, _, symbol = ref.partition("#")
                spans.append(Span(CITE, {"file": file, "symbol": symbol}, length, length + len(label)))
            else:
                spans.append(Span(LINK, {"href": target}, length, length + len(label)))
            out.append(label)
            length += len(label)
        pos = m.end()
    out.append(md[pos:])
    return "".join(out), spans


def render(text: str, spans: list[Span]) -> str:
    """Markdown for ``text`` with ``spans``. Overlapping spans of different
    kinds are rendered innermost-first by start offset; the calculus keeps
    link and cite exclusive so only bold can overlap either."""
    opens: dict[int, list[Span]] = {}
    closes: dict[int, list[Span]] = {}
    for s in spans:
        if s.end <= s.start:
            continue
        opens.setdefault(s.start, []).append(s)
        closes.setdefault(s.end, []).append(s)
    out: list[str] = []
    stack: list[Span] = []
    for i in range(len(text) + 1):
        for s in sorted(closes.get(i, []), key=lambda s: -stack.index(s) if s in stack else 0):
            # close innermost first
            while stack and stack[-1] is not s:
                inner = stack.pop()
                out.append(_close(inner))
                out.append(_open(inner) if inner.end > i else "")
            if stack and stack[-1] is s:
                stack.pop()
            out.append(_close(s))
        for s in sorted(opens.get(i, []), key=lambda s: (s.end, s.kind), reverse=True):
            stack.append(s)
            out.append(_open(s))
        if i < len(text):
            out.append(text[i])
    return "".join(out)


def _open(s: Span) -> str:
    if s.kind == BOLD:
        return "**"
    return "["


def _close(s: Span) -> str:
    if s.kind == BOLD:
        return "**"
    if s.kind == CITE:
        sym = s.attrs.get("symbol") or ""
        return f"](codoc:{s.attrs.get('file', '')}{'#' + sym if sym else ''})"
    return f"]({s.attrs.get('href', '')})"
