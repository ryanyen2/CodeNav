"""Parse ``.codoc/tree.doc.json`` (the webview's authored rich doc) into a
:class:`ParsedTree` — the U2b single-writer input channel.

Single-writer model (U2b): the webview persists its authored intent to
``tree.doc.json`` and NO LONGER writes ``tree.codoc``; the daemon is the sole
``tree.codoc`` writer. So Loop B learns webview edits from THIS file instead of
parsing the daemon-owned text. The output is the same :class:`ParsedTree` shape
``parse.py`` produces, so ``diff_codoc`` and the rest of Loop B are unchanged.

This deliberately makes Python read ``tree.doc.json`` (the prior design kept it
TS-only and bridged via ``edits.json``); the file is plain ProseMirror JSON, so
this is a JSON walk, not a second text parser. The projection mirrors
``pm-doc.ts`` exactly (baseline-aware ``inlineRunsToText`` + the
``blocksToDescriptionText`` normalization + the level→parent stack) so the
doc→ParsedTree→render round-trip stays byte-identical to the text path.

Comments (``> …`` steering) do NOT come through here — the webview hands them to
Loop B one-shot via the ``edits.json`` ``steers`` channel (see ``loop/edits.py``),
the same drain pattern as authorship annotations.
"""
from __future__ import annotations

import json
from pathlib import Path

from codoc.codoc_file.parse import (
    ParsedNode, ParsedTree, extract_refs, normalize_description,
)
from codoc.loop.filenames import DOC_FILENAME

# Mark + node names — mirror pm-doc.ts / agent-proposals.ts (the TS side).
_MARK_INSERTION = "insertion"


def doc_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / DOC_FILENAME


def _inline_text(content: list | None) -> str:
    """Project inline runs to their plain-text form — the exact contract of
    ``pm-doc.inlineRunsToText``: text verbatim, ``codeRef`` → ``[label](codoc:…)``,
    ``hardBreak`` → newline; an uncommitted ``insertion``-marked run is EXCLUDED
    (the baseline projection), a ``deletion``-marked run is kept as plain text."""
    out: list[str] = []
    for n in content or []:
        if not isinstance(n, dict):
            continue
        marks = n.get("marks") or []
        if any(isinstance(m, dict) and m.get("type") == _MARK_INSERTION for m in marks):
            continue  # uncommitted insertion — not part of the baseline
        kind = n.get("type")
        if kind == "text":
            out.append(n.get("text") or "")
        elif kind == "codeRef":
            a = n.get("attrs") or {}
            file = a.get("file") or ""
            symbol = a.get("symbol")
            target = f"{file}#{symbol}" if symbol else file
            out.append(f"[{a.get('label') or ''}](codoc:{target})")
        elif kind == "hardBreak":
            out.append("\n")
    return "".join(out)


def _description(paragraphs: list[list]) -> str:
    """Paragraph text projections → one description string, matching
    ``blocksToDescriptionText``: drop empty paragraphs, join the rest with a blank
    line (one paragraph break), then apply the canonical normalization so the doc
    path produces byte-identical text to the ``tree.codoc`` parser (R19 — otherwise
    trailing whitespace from a doc edit round-trips to a phantom AMEND)."""
    texts = [t for t in (_inline_text(p) for p in paragraphs) if t.strip()]
    return normalize_description("\n\n".join(texts))


def parse_doc(doc: dict) -> ParsedTree:
    """Walk a ProseMirror doc → ParsedTree (pure; no file I/O)."""
    tree = ParsedTree()
    blocks = doc.get("content") or []
    stack: list[tuple[int, ParsedNode]] = []  # (level, node) — parent chain
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if not isinstance(b, dict) or b.get("type") != "featureHeading":
            i += 1
            continue
        attrs = b.get("attrs") or {}
        level = int(attrs.get("level") or 0)
        title = _inline_text(b.get("content")).strip()
        # Gather the paragraphs that belong to this heading (until the next heading).
        paras: list[list] = []
        i += 1
        while i < len(blocks):
            nb = blocks[i]
            if isinstance(nb, dict) and nb.get("type") == "featureHeading":
                break
            if isinstance(nb, dict) and nb.get("type") == "paragraph":
                paras.append(nb.get("content") or [])
            i += 1
        # Parent from the level stack (mirrors parse.py's indent stack).
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent_id = stack[-1][1].id if stack else None
        description = _description(paras)
        node = ParsedNode(
            id=attrs.get("fid") or None,
            title=title,
            description=description,
            parent_id=parent_id,
            retired=bool(attrs.get("retired")),
            local_id=attrs.get("localId") or "",
        )
        node.refs = extract_refs(description)
        tree.nodes.append(node)
        stack.append((level, node))
    return tree


def parse_doc_file(codoc_dir: str | Path) -> ParsedTree | None:
    """Read ``tree.doc.json`` → ParsedTree, or ``None`` when the file is absent
    (no webview has authored a doc yet → fall back to the text path). Tolerant:
    a corrupt/unreadable file degrades to ``None`` rather than crashing Loop B.

    Accepts either the ``DocFile`` wrapper ``{version, doc, suggestions, comments}``
    or a bare ProseMirror doc, matching ``suggestion-model.parseDocFile``."""
    path = doc_path(codoc_dir)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    doc = data if data.get("type") == "doc" else data.get("doc")
    if not isinstance(doc, dict) or doc.get("type") != "doc":
        return None
    return parse_doc(doc)
