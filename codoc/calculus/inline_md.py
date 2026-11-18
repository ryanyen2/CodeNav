"""Inline Markdown ⇄ (plain text, marks by offset).

    **bold**                         → bold
    `code`                           → code
    [label](https://host/path)       → link{href}
    [label](codoc:file.py::Symbol)   → cite{symbol}, the binding key
    \\X  for X in ESCAPABLE           → the literal character

Nesting is link ⊃ bold ⊃ code: a label may hold bold and code, bold may hold
code, code holds nothing. Inside code the only escapes are ``\\``` and ``\\\\``,
so ``list[int]`` and ``**kwargs`` read as typed.

The store allows any overlap of marks; Markdown does not. ``render`` cuts a
bold at every link edge and a code at every link and bold edge, and the pieces
parse back as adjacent same-kind spans, which ``Text.set_mark`` merges.
``parse(render(text, spans))`` therefore reproduces ``(text, spans)`` up to
that merge, for every state the store can hold (fuzzed in the tests).

Offsets are Unicode scalar offsets into the plain text, half-open ``[start, end)``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from codoc.calculus.text import BOLD, CITE, CODE, LINK

# Inline markup, plus the characters that open a block when a line starts with
# them (``# ``, ``- ``, ``+ ``, ``1. ``, ``|``). ``render`` escapes those at the
# start of the text, so a paragraph never reads as a heading, an item or a row.
ESCAPABLE = frozenset("*[]\\#`-+.|")
_CODE_ESCAPABLE = frozenset("`\\")
_TARGET = re.compile(r"\((codoc:[^)\s]+|https?://[^)\s]+)\)")


@dataclass(frozen=True)
class Span:
    kind: str
    attrs: dict
    start: int
    end: int   # exclusive


def parse(md: str) -> tuple[str, list[Span]]:
    """Plain text and its spans. A span that holds others comes before them."""
    out: list[str] = []
    spans: list[Span] = []
    _scan(md, 0, None, out, spans, links=True, bold=True)
    return "".join(out), spans


def _scan(md: str, i: int, stop: str | None, out: list[str], spans: list[Span], *, links: bool, bold: bool) -> int:
    """Scan from ``i`` until ``stop`` (``]`` or ``**``) or the end. Appends
    characters to ``out`` and spans to ``spans``. Returns the index of the stop
    marker, or -1 when a stop was wanted and the text ended first."""
    n = len(md)
    while i < n:
        c = md[i]
        if c == "\\" and i + 1 < n and md[i + 1] in ESCAPABLE:
            out.append(md[i + 1])
            i += 2
        elif c == "`" and (end := _code_end(md, i + 1)) > 0:
            start = _length(out)
            out.append(_unescape_code(md[i + 1:end]))
            spans.append(Span(CODE, {}, start, _length(out)))
            i = end + 1
        elif stop == "]" and c == "]":
            return i
        elif stop == "**" and md.startswith("**", i):
            return i
        elif c == "[" and links and (j := _link(md, i, out, spans)) > 0:
            i = j
        elif bold and md.startswith("**", i) and (j := _bold(md, i, out, spans)) > 0:
            i = j
        else:
            out.append(c)
            i += 1
    return -1 if stop else n


def _length(out: list[str]) -> int:
    return sum(len(s) for s in out)


def _code_end(md: str, i: int) -> int:
    """Index of the backtick closing a code span whose content starts at
    ``i``, or -1 (no closing backtick on this line, or an empty span)."""
    j = i
    while j < len(md) and md[j] != "\n":
        if md[j] == "\\" and j + 1 < len(md) and md[j + 1] in _CODE_ESCAPABLE:
            j += 2
        elif md[j] == "`":
            return j if j > i else -1
        else:
            j += 1
    return -1


def _unescape_code(raw: str) -> str:
    return re.sub(r"\\([`\\])", r"\1", raw)


def _link(md: str, i: int, out: list[str], spans: list[Span]) -> int:
    """``[label](target)`` at ``i``: append it and return the index after it,
    or -1 when this bracket does not open a link."""
    label: list[str] = []
    inner: list[Span] = []
    close = _scan(md, i + 1, "]", label, inner, links=False, bold=True)
    if close < 0 or not (m := _TARGET.match(md, close + 1)):
        return -1
    start, plain, target = _length(out), "".join(label), m.group(1)
    if target.startswith("codoc:"):
        spans.append(Span(CITE, {"symbol": target[len("codoc:"):]}, start, start + len(plain)))
    else:
        spans.append(Span(LINK, {"href": target}, start, start + len(plain)))
    spans.extend(Span(s.kind, s.attrs, s.start + start, s.end + start) for s in inner)
    out.append(plain)
    return m.end()


def _bold(md: str, i: int, out: list[str], spans: list[Span]) -> int:
    """``**text**`` at ``i``: append it and return the index after it, or -1
    when it is not closed on this line or holds nothing."""
    text: list[str] = []
    inner: list[Span] = []
    close = _scan(md, i + 2, "**", text, inner, links=False, bold=False)
    plain = "".join(text)
    if close < 0 or not plain or "\n" in plain:
        return -1
    start = _length(out)
    spans.append(Span(BOLD, {}, start, start + len(plain)))
    spans.extend(Span(s.kind, s.attrs, s.start + start, s.end + start) for s in inner)
    out.append(plain)
    return close + 2


def render(text: str, spans: list[Span]) -> str:
    """Markdown for ``text`` with ``spans``. Characters that would read as
    markup are escaped."""
    links = sorted((s for s in spans if s.kind in (LINK, CITE) and s.end > s.start), key=lambda s: s.start)
    bolds = _cut(spans, BOLD, [p for l in links for p in (l.start, l.end)])
    codes = _cut(spans, CODE, [p for s in links + bolds for p in (s.start, s.end)])
    opens: dict[int, list[Span]] = {}
    closes: dict[int, list[Span]] = {}
    for s in links + bolds + codes:
        opens.setdefault(s.start, []).append(s)
        closes.setdefault(s.end, []).append(s)
    in_label = _covered(len(text), links)
    in_code = _covered(len(text), codes)
    bold_edge = {i for s in bolds for i in (s.start, s.end)}
    out: list[str] = []
    for i in range(len(text) + 1):
        for s in sorted(closes.get(i, []), key=lambda s: _DEPTH[s.kind], reverse=True):   # innermost first
            out.append(_CLOSE[s.kind](s))
        for s in sorted(opens.get(i, []), key=lambda s: _DEPTH[s.kind]):                  # outermost first
            out.append(_OPEN[s.kind])
        if i < len(text):
            out.append(_escaped(text, i, in_label[i], in_code[i], i in bold_edge or (i + 1) in bold_edge))
    return "".join(out)


def _cut(spans: list[Span], kind: str, edges: list[int]) -> list[Span]:
    """The spans of ``kind``, each cut at every edge that falls inside it."""
    out: list[Span] = []
    for s in spans:
        if s.kind != kind or s.end <= s.start:
            continue
        cuts = sorted({s.start, s.end} | {p for p in edges if s.start < p < s.end})
        out.extend(Span(kind, {}, lo, hi) for lo, hi in zip(cuts, cuts[1:]))
    return out


def _covered(length: int, spans: list[Span]) -> list[bool]:
    flags = [False] * (length + 1)
    for s in spans:
        for i in range(s.start, s.end):
            flags[i] = True
    return flags


def _close_link(s: Span) -> str:
    if s.kind == CITE:
        return f"](codoc:{s.attrs.get('symbol', '')})"
    return f"]({s.attrs.get('href', '')})"


_DEPTH = {LINK: 0, CITE: 0, BOLD: 1, CODE: 2}
_OPEN = {LINK: "[", CITE: "[", BOLD: "**", CODE: "`"}
_CLOSE = {LINK: _close_link, CITE: _close_link, BOLD: lambda s: "**", CODE: lambda s: "`"}


def _escaped(text: str, i: int, in_label: bool, in_code: bool, at_bold_edge: bool) -> str:
    c = text[i]
    if in_code:
        return "\\" + c if c in _CODE_ESCAPABLE else c
    if c == "\\":
        return "\\\\"
    if c == "*" and (at_bold_edge or (i > 0 and text[i - 1] == "*") or (i + 1 < len(text) and text[i + 1] == "*")):
        return "\\*"
    if c == "]" and in_label:
        return "\\]"
    if c in "[`":
        return "\\" + c       # a plain bracket never opens a link, a plain backtick never opens code
    if _opens_block(text, i):
        return "\\" + c
    return c


def _opens_block(text: str, i: int) -> bool:
    """Would ``text[i]`` make the line read as a heading, a list item or a
    table row? True for a leading ``#`` or ``|``, a leading ``-``/``+``/``*``
    before a space, and the dot of a leading ``12. ``."""
    c = text[i]
    if i == 0:
        return c in "#|" or (c in "-+*" and text[1:2] == " ")
    return c == "." and i <= 9 and text[:i].isdigit() and text[i + 1:i + 2] == " "
