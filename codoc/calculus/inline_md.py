"""Inline Markdown ⇄ (plain text, marks by offset).

The three inline forms codoc's prose uses, and one escape:

    **bold**                         → bold
    [label](https://host/path)       → link{href}
    [label](codoc:file.py#Symbol)    → cite{file, symbol}
    \\X  for X in  * [ ] \\ # `        → the literal character

Parsing is a left-to-right scan: an escape pair is always a literal, a link is
tried before bold at each position, a link's label may hold bold, bold may not
hold a link. Anything that is not one of these forms is plain text. The
calculus keeps link and cite exclusive and merges same-kind overlaps, so a bold
that crosses a link edge is rendered as two bolds and parses back to two marks
that ``set_mark`` merges. ``parse(render(text, spans))`` reproduces
``(text, spans)`` for every state the store can hold (fuzzed in the tests).

Offsets are Unicode scalar offsets into the plain text, half-open ``[start, end)``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from codoc.calculus.text import BOLD, CITE, LINK

ESCAPABLE = frozenset("*[]\\#`")
_LINK = re.compile(r"\[(?P<label>(?:[^\]\n\\]|\\.)*)\]\((?P<target>codoc:[^)\s]+|https?://[^)\s]+)\)")
_BOLD = re.compile(r"\*\*(?P<bold>(?:[^*\n\\]|\\.|\*(?!\*))+?)\*\*")


@dataclass(frozen=True)
class Span:
    kind: str
    attrs: dict
    start: int
    end: int   # exclusive


def parse(md: str) -> tuple[str, list[Span]]:
    """Plain text and its spans, in document order (a label's bold follows the
    link that holds it)."""
    text, spans = _scan(md, links=True)
    return "".join(text), spans


def _scan(md: str, *, links: bool) -> tuple[list[str], list[Span]]:
    out: list[str] = []
    spans: list[Span] = []
    i = n = 0
    n = len(md)
    length = 0
    while i < n:
        c = md[i]
        if c == "\\" and i + 1 < n and md[i + 1] in ESCAPABLE:
            out.append(md[i + 1])
            length += 1
            i += 2
            continue
        if c == "[" and links and (m := _LINK.match(md, i)):
            label, inner = _scan(m.group("label"), links=False)
            plain = "".join(label)
            target = m.group("target")
            if target.startswith("codoc:"):
                file, _, symbol = target[len("codoc:"):].partition("#")
                spans.append(Span(CITE, {"file": file, "symbol": symbol}, length, length + len(plain)))
            else:
                spans.append(Span(LINK, {"href": target}, length, length + len(plain)))
            spans.extend(Span(s.kind, s.attrs, s.start + length, s.end + length) for s in inner)
            out.append(plain)
            length += len(plain)
            i = m.end()
            continue
        if c == "*" and (m := _BOLD.match(md, i)):
            inner_text, _ = _scan(m.group("bold"), links=False)
            plain = "".join(inner_text)
            spans.append(Span(BOLD, {}, length, length + len(plain)))
            out.append(plain)
            length += len(plain)
            i = m.end()
            continue
        out.append(c)
        length += 1
        i += 1
    return out, spans


def render(text: str, spans: list[Span]) -> str:
    """Markdown for ``text`` with ``spans``. Plain characters that would read
    as markup, next to what the renderer emits or on their own, are escaped;
    a bold crossing a link edge is split there."""
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
    in_label = [False] * (len(text) + 1)
    for l in links:
        for i in range(l.start, l.end):
            in_label[i] = True
    bold_edge = {i for s in bolds for i in (s.start, s.end)}
    out: list[str] = []
    for i in range(len(text) + 1):
        # close bold before the link that holds it; open a link before its bold
        for s in sorted(closes.get(i, []), key=lambda s: s.kind != BOLD):
            out.append("**" if s.kind == BOLD else _close_link(s))
        for s in sorted(opens.get(i, []), key=lambda s: s.kind == BOLD):
            out.append("**" if s.kind == BOLD else "[")
        if i < len(text):
            out.append(_escaped(text, i, in_label[i], i in bold_edge or (i + 1) in bold_edge))
    return "".join(out)


def _escaped(text: str, i: int, in_label: bool, at_bold_edge: bool) -> str:
    c = text[i]
    if c == "\\":
        return "\\\\"
    if c == "*" and (at_bold_edge or (i > 0 and text[i - 1] == "*") or (i + 1 < len(text) and text[i + 1] == "*")):
        return "\\*"
    if c == "]" and in_label:
        return "\\]"
    if c == "[":
        return "\\["          # a plain bracket never opens a link, whatever follows it
    if i == 0 and c in "#`":
        return "\\" + c
    return c


def _close_link(s: Span) -> str:
    if s.kind == CITE:
        sym = s.attrs.get("symbol") or ""
        return f"](codoc:{s.attrs.get('file', '')}{'#' + sym if sym else ''})"
    return f"]({s.attrs.get('href', '')})"
