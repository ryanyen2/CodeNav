"""A Jupyter notebook, read as the Python it is.

A notebook is not a new kind of readable file the way a settings file is. It HAS
symbols — its cells hold the same defs, classes and statements a script does — so it
belongs here, behind the adapter contract, and every seam that asks
``detect_language`` and then ``get_adapter`` works on ``.ipynb`` unchanged: the diff's
clean-parse check, the hook's line mapping, the dependency graph, coverage arithmetic,
the payload budget, bootstrap's file ordering.

What the adapter has to add is the two things the JSON holds and a ``.py`` file does
not:

**The prose.** A notebook's markdown cells are the author's own account of what the
code is doing, in the same file, already at the altitude a feature description wants.
They ride into the synthetic document as raw string literals — statements, not
comments — and that choice is load-bearing twice over. A comment is not part of a
chunk: module-level glue accumulates in runs that exclude comment nodes, so prose
written as ``#`` lines would reach no prompt at all, and a section that is only prose
would produce no chunk for a feature to bind to. And a comment is not part of a chunk's
IDENTITY, so a rewritten paragraph would not even be noticed. That exclusion earns its
keep for code, where a reflowed comment is not a change; a notebook inverts the case,
because there the markdown IS the authored intent and a reworded step is exactly the
change a fresh description should follow.

**The steps.** A markdown heading names the run of statements under it — ``## Load the
data`` is what those five cells ARE — so headings partition the notebook into sections
and each section's statements become one chunk addressed by the author's own step name.
Definitions inside a section are members of it, exactly as a method is a member of its
class: the heading states what the function is there for. Before the first heading, and
in a notebook that has no headings at all, the addresses are a script's own —
``::__module__`` for the statements and bare names for the definitions — so a notebook
of plain cells produces exactly the chunks the equivalent ``.py`` file would.

Sections are FLAT: ``### Load`` under ``## Data`` is ``load``, not ``data.load``. A
notebook's headings are a reading ORDER, not a namespace — the cells under ``## Train``
are the next step, not the property of anything above them — and a dotted address would
claim a containment the file does not have.

Identity comes from the cells' SOURCE alone. Outputs, ``execution_count`` and every
other field a run rewrites are not in the synthetic document at all, so re-running a
notebook is not a change to any chunk.

What is deliberately not attempted: IPython that is not Python. A ``!pip install`` or
``%%bash`` line is commented out rather than parsed, and a cell that still does not
parse is commented out whole — one shell line at the top of a notebook must not make
the entire file read as damaged, which is what holds Loop A's removals. "Does not
parse" is ``PythonAdapter.reads_cleanly``, both of its readers: a cell has to be
rejected by the grammar AND by the interpreter before it is commented out, or a cell
using syntax newer than the bundled grammar would lose every definition in it. Two costs come
with that, both preferred to the alternative: a genuinely half-typed cell reads as
clean rather than as damage (the rarer case, since Jupyter writes the file on save),
and a section whose only cell is shell contributes no chunk, which is the honest report
that codoc did not read it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from codoc.lang.base import Chunk, SymbolRef
from codoc.lang.python import PythonAdapter

LANGUAGE_NAME = "notebook"

#: Parsed instead of the cells when the JSON itself cannot be read. It has to FAIL to
#: parse: a notebook we could not open yields no chunks, and the difference between
#: "this file defines nothing" and "we could not read it" is the difference between
#: retiring every feature bound to it and holding them (``parses_cleanly`` → False →
#: ``loop/diff._hold_unparseable_removals``). Raw JSON would not do — ``{"cells": []}``
#: is a perfectly good Python expression, so the damaged file would report itself clean.
_UNPARSEABLE = "(\n"

#: A line IPython runs and Python cannot: ``!ls``, ``%timeit``, ``%%bash``.
_MAGIC_LINE = re.compile(r"^\s*[!%]")
#: ``df.head?`` / ``??np.mean`` — IPython's help, a syntax error to Python.
_HELP_LINE = re.compile(r"^\s*(?:\?{1,2}\s*\S+|\S+\s*\?{1,2})\s*$")
#: An ATX markdown heading. Setext (``---`` underlines) is not read: it cannot be told
#: from a horizontal rule without looking ahead, and notebooks overwhelmingly use ATX.
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")

#: The two ways to open a multi-line string, tried in this order for the prose.
_QUOTES = ('"""', "'" * 3)


@dataclass
class Cell:
    """One notebook cell, reduced to the two things this module reads."""
    kind: str      # "code" | "markdown" | "raw"
    source: str    # the cell's text, newlines preserved, no trailing newline


def _joined(value: object) -> str:
    """A cell's source, whether nbformat wrote it as a list of lines or one string.

    The format's own rule is that every entry of the list ends in a newline except the
    last, so the entries are concatenated. A list where NONE of them does is repaired by
    joining on newlines instead: it comes from tools that assemble notebooks by hand,
    and concatenating it would collapse a whole cell onto one line — which parses as
    nothing, so the cell would leave the tree silently.
    """
    if isinstance(value, list):
        parts = [str(part) for part in value]
        if len(parts) > 1 and not any(part.endswith("\n") for part in parts):
            return "\n".join(parts)
        return "".join(parts)
    return str(value) if value is not None else ""


def read_cells(raw: str) -> list[Cell] | None:
    """The notebook's cells in order, or None if this is not a notebook.

    Reads nbformat 4 (``cells``) and 3 (``worksheets[].cells``, whose code cells keep
    their text under ``input`` and whose headings are a cell type rather than markdown),
    because a repository's notebooks are as old as the repository. None — rather than an
    empty list — for anything that will not read as a notebook, so a caller can tell
    "read it, it has no cells" from "could not read it".
    """
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(doc, dict):
        return None

    raw_cells: list[object] = []
    if isinstance(doc.get("cells"), list):
        raw_cells = list(doc["cells"])
    elif isinstance(doc.get("worksheets"), list):
        for sheet in doc["worksheets"]:
            if isinstance(sheet, dict) and isinstance(sheet.get("cells"), list):
                raw_cells.extend(sheet["cells"])
    else:
        return None

    cells: list[Cell] = []
    for item in raw_cells:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("cell_type", "code"))
        text = _joined(item.get("source", item.get("input", "")))
        if kind == "heading":
            # nbformat 3 held headings as their own cell type. Written back out as the
            # markdown they became, so the section rule below has one thing to read.
            level = item.get("level", 1)
            level = level if isinstance(level, int) and 1 <= level <= 6 else 1
            cells.append(Cell("markdown", f"{'#' * level} {text.strip()}"))
            continue
        if kind not in {"code", "markdown", "raw"}:
            kind = "raw"
        cells.append(Cell(kind, text.rstrip("\n")))
    return cells


def _comment(line: str) -> str:
    """*line* as a Python comment, keeping its indentation.

    Indentation is preserved so a commented-out magic inside a block still reads where
    it was written. It does not save the block — a body of nothing but comments is a
    syntax error, and the whole-cell fallback then takes it — but a reader of the
    synthetic source sees the notebook's shape.
    """
    stripped = line.lstrip()
    if not stripped:
        return "#"
    indent = line[: len(line) - len(stripped)]
    return f"{indent}# {stripped}"


def _code_lines(source: str) -> list[str] | None:
    """A code cell's lines as Python, or None if none of it can be.

    None for a cell magic (``%%bash`` owns the whole cell, so nothing in it is Python)
    so the caller comments the cell out entire. Otherwise the per-line magics and help
    lines are commented and the rest is handed back as written.
    """
    lines = source.split("\n")
    for line in lines:
        if line.strip():
            if line.lstrip().startswith("%%"):
                return None
            break
    return [
        _comment(line)
        if _MAGIC_LINE.match(line) or _HELP_LINE.match(line)
        else line
        for line in lines
    ]


def _prose_blocks(text: str) -> list[list[str]]:
    """A markdown cell's lines, split into one block per heading.

    Split because a section boundary has to fall BETWEEN statements: each block becomes
    one string literal, and a heading four lines into a cell would otherwise put the
    boundary inside a literal, which is not a place a document can be cut. Blank lines
    at a block's edges are dropped, so a literal starts on the heading and ends on the
    last thing said under it.
    """
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.split("\n"):
        if _HEADING.match(line) and current:
            blocks.append(current)
            current = []
        current.append(line)
    blocks.append(current)

    trimmed: list[list[str]] = []
    for block in blocks:
        while block and not block[0].strip():
            block = block[1:]
        while block and not block[-1].strip():
            block = block[:-1]
        if block:
            trimmed.append(block)
    return trimmed


def _prose_literal(block: list[str]) -> list[tuple[str, str]] | None:
    """*block* as a raw string literal: one synthetic line per prose line.

    Raw, and quoted with whichever triple-quote the prose does not itself use, so the
    author's text is carried through as written. Escaping would put backslashes into a
    paragraph a prompt is about to read and a person is about to be shown as the reason
    for a description. None when the prose uses both quote styles — the one case with
    nothing safe to wrap it in, where the caller falls back to comments and loses that
    block alone.

    The quotes open on the first prose line and close on the last, rather than taking
    lines of their own, so every synthetic line still stands for a line of the file and
    the offsets built from them stay honest.
    """
    text = "\n".join(block)
    quote = next((q for q in _QUOTES if q not in text), None)
    if quote is None:
        return None
    # A raw string may not end on a backslash, and its closing quote may not touch a
    # quote character of the same kind. One empty line settles both.
    if block[-1].endswith("\\") or block[-1].endswith(quote[0]):
        block = block + [""]
    last = len(block) - 1
    return [
        (f"{'r' + quote if i == 0 else ''}{line}{quote if i == last else ''}", line)
        for i, line in enumerate(block)
    ]


@dataclass
class _Doc:
    """The synthetic Python document, and the two indexes that lead back to the file."""
    lines: list[str]              # the synthetic source, one entry per line
    probes: list[str]             # the notebook line each came from ("" if inserted)
    headings: dict[int, str]      # line index → the heading title starting there


def _synthesize(cells: list[Cell]) -> _Doc:
    """Build one Python document out of the cells, in notebook order.

    Cells are separated by a blank line so a definition opening a cell is not glued to
    the statement that ended the last one.
    """
    adapter = PythonAdapter()
    doc = _Doc(lines=[], probes=[], headings={})

    def emit(line: str, probe: str) -> None:
        doc.lines.append(line)
        doc.probes.append(probe)

    for cell in cells:
        if doc.lines:
            emit("", "")
        if cell.kind == "code":
            body = _code_lines(cell.source)
            if body is not None and not adapter.reads_cleanly("\n".join(body)):
                body = None
            if body is None:
                body = [_comment(line) for line in cell.source.split("\n")]
            for original, line in zip(cell.source.split("\n"), body):
                emit(line, original)
            continue
        for block in _prose_blocks(cell.source):
            heading = _HEADING.match(block[0])
            written = _prose_literal(block) or [(_comment(l), l) for l in block]
            if heading:
                doc.headings[len(doc.lines)] = heading.group(2)
            for line, probe in written:
                emit(line, probe)
    return doc


def _escapes(text: str) -> list[str]:
    """*text* as it could appear inside the notebook's JSON, likeliest form first.

    Two forms because the writer's choice of ``ensure_ascii`` is recorded nowhere in the
    file, and a line with an accent or a CJK character is written one way by ``nbformat``
    and the other by a tool that reached for ``json.dump`` directly.
    """
    forms = [json.dumps(text, ensure_ascii=False)[1:-1]]
    ascii_form = json.dumps(text)[1:-1]
    if ascii_form != forms[0]:
        forms.append(ascii_form)
    return forms


def _raw_offsets(raw: str, probes: list[str]) -> list[int]:
    """Where each synthetic line's own text sits in the notebook file.

    These are anchors, not a slice: nothing re-reads a notebook chunk out of the file by
    its offsets, and nothing could — the bytes between two of a chunk's lines are JSON
    punctuation and possibly a megabyte of base64 output. What the offsets are actually
    asked for is ORDER (bootstrap reads a file's chunks by ``start_byte``, and a
    notebook's order is its narrative) and an approximate line number for a hook
    reporting where an edit landed. So the search is one monotone forward pass: each line
    is looked for after the last was found, which keeps the sequence increasing even
    where a line's text repeats, and a line that cannot be found inherits the position of
    the one before it rather than resetting the scan.
    """
    offsets: list[int] = []
    cursor = 0
    for probe in probes:
        text = probe.strip()
        found = -1
        if text:
            for form in _escapes(text):
                found = raw.find(form, cursor)
                if found >= 0:
                    cursor = found + len(form)
                    break
        offsets.append(found if found >= 0 else cursor)
    return offsets


def _slug(title: str) -> str:
    """A heading as an address: its own words, lowercased, joined by dashes.

    Word characters rather than ASCII letters, because the tree may be authored in a
    language that has none of the latter and ``## 加载数据`` names its section as plainly
    as ``## Load the data`` does. Dots are dropped rather than kept: a dot in a symbol
    path means "owned by", and ``## Step 1.2`` would otherwise address a section as the
    member of a section named ``step 1``.
    """
    cleaned = re.sub(r"[^\w]+", "-", title.replace(".", " "), flags=re.UNICODE)
    return cleaned.strip("-_").lower()


def _uniquify(name: str, seen: set[str]) -> str:
    """*name*, or the next free ``name[n]``.

    Same convention as a repeated settings header, for the same reason: two ``## Results``
    headings are two sections, both have to be addressable, and a suffix that reads as an
    index is easier to place in a citation than a renamed heading.
    """
    if name not in seen:
        seen.add(name)
        return name
    index = 1
    while f"{name}[{index}]" in seen:
        index += 1
    seen.add(f"{name}[{index}]")
    return f"{name}[{index}]"


def _sections(doc: _Doc) -> list[tuple[str | None, int, int]]:
    """The document split at its headings: ``(name, first line, last line + 1)``.

    The run before the first heading is unnamed, and that is the whole of what makes a
    heading-less notebook read as a script: with no headings there is one unnamed section
    spanning the file, so the delegated walk's own addresses come through untouched.
    """
    starts = sorted(doc.headings)
    bounds = ([0] if not starts or starts[0] != 0 else []) + starts
    out: list[tuple[str | None, int, int]] = []
    for i, start in enumerate(bounds):
        end = bounds[i + 1] if i + 1 < len(bounds) else len(doc.lines)
        out.append((doc.headings.get(start), start, end))
    return out


def _chunks(file: str, raw: str) -> list[Chunk]:
    """Every chunk the notebook contributes, in reading order.

    The walk itself is not reimplemented: each section's text is handed to the Python
    adapter, which already merges the definitions of one name, keeps a decorated
    definition whole, and treats a ``def`` under an ``if`` as belonging to the scope
    around it. This function decides the ADDRESSES — the part a notebook changes — and
    maps the section-local ranges it gets back onto the file.
    """
    cells = read_cells(raw)
    if cells is None:
        return []
    doc = _synthesize(cells)
    if not doc.lines:
        return []
    offsets = _raw_offsets(raw, doc.probes)
    adapter = PythonAdapter()
    seen: set[str] = set()
    chunks: list[Chunk] = []

    for name, start, end in _sections(doc):
        text = "\n".join(doc.lines[start:end])
        if not text.strip():
            continue
        # The section's own name is settled BEFORE its chunks, and registered whether or
        # not it has statements of its own. A second ``## Train`` is a second section, so
        # its members have to be addressed under the name that section actually got —
        # naming each chunk independently would file them under the first one, which is
        # the one thing an address may not do.
        section = None if name is None else _uniquify(_slug(name) or "section", seen)
        for inner in sorted(adapter.extract_chunks("", text), key=lambda c: c.start_byte):
            local = inner.symbol_path.split("::", 1)[1]
            if section is None:
                address = _uniquify(local, seen)
            elif local == "__module__":
                address = section          # the name was claimed with the section
            else:
                address = _uniquify(f"{section}.{local}", seen)
            first = min(start + text[: inner.start_byte].count("\n"), len(offsets) - 1)
            last = start + text[: max(inner.end_byte - 1, 0)].count("\n")
            last = min(max(last, first), len(offsets) - 1)
            probe = doc.probes[last].strip()
            span = len(_escapes(probe)[0]) if probe else 1
            chunks.append(Chunk(
                symbol_path=f"{file}::{address}",
                file=file,
                start_byte=offsets[first],
                end_byte=max(offsets[last] + span, offsets[first] + 1),
                source=inner.source,
            ))
    return chunks


class NotebookAdapter:
    """The Python adapter, over the document a notebook's cells describe.

    Everything that reads code delegates: the synthetic source IS Python, so the parse
    tree, the token stream, the reference walk and the queries are the Python adapter's
    own and there is no second dialect to keep in step with it. Only ``extract_chunks``
    differs, because only the addresses do.
    """
    language = LANGUAGE_NAME

    def synthetic_source(self, source: str) -> str:
        """The Python document *source* describes.

        Public because it is the honest answer to "what did codoc read here", and every
        other method here is defined in terms of it.

        It takes a notebook OR a chunk of one. The identity walk and the reference walk
        are handed a chunk's own source, which is already this synthetic Python and has
        to be parsed as it stands — routing that through the cell reader would report
        every chunk of every notebook as the same unreadable file, which is a hash that
        never moves and a graph with no edges. So a text that is not a notebook is parsed
        as Python, unless it opens like a JSON document: then it IS a notebook we failed
        to read, and it has to parse as damage.
        """
        cells = read_cells(source)
        if cells is not None:
            return "\n".join(_synthesize(cells).lines)
        return _UNPARSEABLE if source.lstrip().startswith("{") else source

    def extract_chunks(self, file: str, source: str) -> list[Chunk]:
        return _chunks(file, source)

    @property
    def comment_node_kinds(self) -> set[str]:  # type: ignore[override]
        return PythonAdapter().comment_node_kinds

    def resolve_symbol_path(self, source: str, symbol_path: str) -> tuple[int, int] | None:
        qualified = symbol_path.split("::", 1)[1] if "::" in symbol_path else symbol_path
        for chunk in _chunks("", source):
            if chunk.symbol_path == f"::{qualified}":
                return (chunk.start_byte, chunk.end_byte)
        return None

    def run_ts_query(
        self, source: str, query_str: str,
        scope: tuple[int, int] | None = None,
    ) -> list[tuple[int, int]]:
        """Run *query_str* over the synthetic document.

        The ranges returned are the synthetic document's, and a *scope* is read the same
        way, because a query about a notebook is a question about its code and the JSON
        around it has no answers. A caller holding a chunk's file offsets cannot use them
        here — which is why none does: the reference walk takes a chunk's source instead.
        """
        return PythonAdapter().run_ts_query(
            self.synthetic_source(source), query_str, scope)

    def references_in_chunk(self, chunk_source: str, file: str) -> list[SymbolRef]:
        # A chunk's source is already synthetic Python, so this needs no conversion.
        return PythonAdapter().references_in_chunk(chunk_source, file)

    def reads_cleanly(self, source: str) -> bool:
        """Whether the document this notebook describes is whole.

        Asked of the synthetic Python rather than of the JSON, for the reason every
        other method here delegates: what codoc read is the document, and a notebook we
        could not open reaches this as ``_UNPARSEABLE``, which both readers reject.
        """
        return PythonAdapter().reads_cleanly(self.synthetic_source(source))

    def parse(self, source: str):
        return PythonAdapter().parse(self.synthetic_source(source))

    def token_stream(self, source: str, exclude_comment_nodes: bool = True) -> list[str]:
        return PythonAdapter().token_stream(
            self.synthetic_source(source), exclude_comment_nodes)
