"""Inline Markdown ⇄ (plain text, marks by offset).

The three inline forms codoc's prose uses, and one escape:

    **bold**                         → bold
    [label](https://host/path)       → link{href}
    [label](codoc:file.py#Symbol)    → cite{file, symbol}
    \\X  for X in  * [ ] \\ # `        → the literal character

Parsing is exact and total: anything that is not one of these forms is plain
text, including a lone ``*`` or an unmatched bracket. A link's label may hold
bold; bold may not hold a link (the calculus keeps link and cite exclusive and
merges same-kind overlaps, so a bold that straddles a link edge is rendered as
two bolds, one inside the label and one outside, and parses back to two marks
that ``set_mark`` merges). ``parse(render(text, spans))`` therefore reproduces
``(text, spans)`` for every state the store can hold, which is what makes
``export → import`` and ``ingest`` fixed points.

Offsets are Unicode scalar offsets into the plain text, half-open ``[start, end)``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from codoc.calculus.text import BOLD, CITE, LINK

ESCAPABLE = frozenset("*[]\\#`")
_UNESCAPE = re.compile(r"\\([*\[\]\\#`])")
_LINK = re.compile(
    r"(?<!\\)\[(?P<label>(?:[^\]\n\\]|\\.)*)\]\((?P<target>codoc:[^)\s]+|https?://[^)\s]+)\)"
)
_BOLD = re.compile(r"(?<!\\)\*\*(?P<bold>(?:[^*\n\\]|\\.|\*(?!\*))+?)\*\*")


@dataclass(frozen=True)
class Span:
    kind: str
    attrs: dict
    start: int
    end: int   # exclusive


def parse(md: str) -> tuple[str, list[Span]]:
    """Plain text and its spans, in document order (a label's bold follows the
    link that holds it)."""
    text: list[str] = []
    spans: list[Span] = []
    length = 0
    pos = 0

    def emit(segment: str) -> None:
        nonlocal length
        plain, inner = _parse_bold(segment)
        text.append(plain)
        spans.extend(Span(s.kind, s.attrs, s.start + length, s.end + length) for s in inner)
        length += len(plain)

    for m in _LINK.finditer(md):
        emit(md[pos:m.start()])
        label_plain, label_spans = _parse_bold(m.group("label"))
        target = m.group("target")
        if target.startswith("codoc:"):
            file, _, symbol = target[len("codoc:"):].partition("#")
            spans.append(Span(CITE, {"file": file, "symbol": symbol}, length, length + len(label_plain)))
        else:
            spans.append(Span(LINK, {"href": target}, length, length + len(label_plain)))
        spans.extend(Span(s.kind, s.attrs, s.start + length, s.end + length) for s in label_spans)
        text.append(label_plain)
        length += len(label_plain)
        pos = m.end()
    emit(md[pos:])
    return "".join(text), spans


def _parse_bold(s: str) -> tuple[str, list[Span]]:
    out: list[str] = []
    spans: list[Span] = []
    pos = length = 0
    for m in _BOLD.finditer(s):
        plain = _unescape(s[pos:m.start()])
        out.append(plain)
        length += len(plain)
        inner = _unescape(m.group("bold"))
        spans.append(Span(BOLD, {}, length, length + len(inner)))
        out.append(inner)
        length += len(inner)
        pos = m.end()
    out.append(_unescape(s[pos:]))
    return "".join(out), spans


def _unescape(s: str) -> str:
    return _UNESCAPE.sub(r"\1", s)


def render(text: str, spans: list[Span]) -> str:
    """Markdown for ``text`` with ``spans``. Plain characters that would read
    as markup are escaped; a bold crossing a link edge is split there."""
    links = sorted((s for s in spans if s.kind in (LINK, CITE) and s.end > s.start), key=lambda s: s.start)
    bolds: list[Span] = []
    for b in (s for s in spans if s.kind == BOLD and s.end > s.start):
        cuts = sorted({b.start, b.end} | {p for l in links for p in (l.start, l.end) if b.start < p < b.end})
        bolds.extend(Span(BOLD, {}, lo, hi) for lo, hi in zip(cuts, cuts[1:]))
    opens: dict[int, list[Span]] = {}
    closes: dict[int, list[Span]] = {}
    for s in links + bolds:
        opens.setdefault(s.start, []).append(s)
        closes.setdefault(s.end, []).append(s)
    esc = _escapes(text)
    out: list[str] = []
    for i in range(len(text) + 1):
        # close bold before the link that holds it; open a link before its bold
        for s in sorted(closes.get(i, []), key=lambda s: s.kind != BOLD):
            out.append("**" if s.kind == BOLD else _close_link(s))
        for s in sorted(opens.get(i, []), key=lambda s: s.kind == BOLD):
            out.append("**" if s.kind == BOLD else "[")
        if i < len(text):
            out.append(esc[i])
    return "".join(out)


def _escapes(text: str) -> list[str]:
    """Each character as it must be written so that it reads back as itself."""
    out = list(text)
    for i, c in enumerate(text):
        if c == "\\":
            out[i] = "\\\\"
        elif c == "*" and ((i > 0 and text[i - 1] == "*") or (i + 1 < len(text) and text[i + 1] == "*")):
            out[i] = "\\*"
        elif c == "[" and _LINK.match(text, i):
            out[i] = "\\["
        elif i == 0 and c in "#`":
            out[i] = "\\" + c
    return out


def _close_link(s: Span) -> str:
    if s.kind == CITE:
        sym = s.attrs.get("symbol") or ""
        return f"](codoc:{s.attrs.get('file', '')}{'#' + sym if sym else ''})"
    return f"]({s.attrs.get('href', '')})"
