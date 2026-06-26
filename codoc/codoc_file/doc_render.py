"""doc_render.py — render the store's feature tree into a ProseMirror doc
(``tree.doc.json`` shape), the inverse of :mod:`codoc.codoc_file.doc_parse`.

The VS Code host normally authors ``tree.doc.json`` (it is the webview-authoritative
rich doc, U2b). But the ``codoc serve`` hub is a file-channel client with no editor
attached, so on a workspace that has never been opened in VS Code the doc file is
empty and the hub would serve a blank page. This module lets the hub derive the doc
itself from ``tree.codoc`` (already on disk), so it is self-sufficient.

It is the exact inverse of ``doc_parse._inline_text`` / ``_description``: a feature
becomes a ``featureHeading`` (carrying ``fid`` + outliner ``level`` = tree depth)
followed by one ``paragraph`` per description paragraph, with inline
``[label](codoc:file#symbol)`` citations re-materialised as ``codeRef`` atoms. The
round-trip ``build_doc(parse_text(t))`` → ``parse_doc`` recovers the same titles +
descriptions (guarded by a test), so a hub-rendered doc is indistinguishable from an
editor-authored one to the rest of the pipeline.
"""
from __future__ import annotations

import re

from codoc.codoc_file.parse import ParsedTree, parse_text

# Inline code citation, mirroring parse._REF_RE: [label](codoc:file#symbol), symbol optional.
_REF_RE = re.compile(r"\[(?P<label>[^\]]*)\]\(codoc:(?P<file>[^)#]+)(?:#(?P<symbol>[^)]+))?\)")
# A paragraph break is one or more blank lines.
_PARA_SPLIT = re.compile(r"\n\s*\n")


def _inline_runs(text: str) -> list[dict]:
    """Project a paragraph's text into PM inline nodes: plain text + ``codeRef`` atoms
    for each ``[label](codoc:…)`` citation. The inverse of ``doc_parse._inline_text``."""
    runs: list[dict] = []
    pos = 0
    for m in _REF_RE.finditer(text):
        if m.start() > pos:
            runs.append({"type": "text", "text": text[pos:m.start()]})
        runs.append({
            "type": "codeRef",
            "attrs": {
                "label": m.group("label") or "",
                "file": m.group("file") or "",
                "symbol": m.group("symbol") or "",
            },
        })
        pos = m.end()
    if pos < len(text):
        runs.append({"type": "text", "text": text[pos:]})
    return runs


def _paragraphs(description: str) -> list[dict]:
    """Description text → PM ``paragraph`` blocks (split on blank lines). Empty
    paragraphs are dropped, matching ``doc_parse._description``'s projection."""
    blocks: list[dict] = []
    for chunk in _PARA_SPLIT.split(description or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        runs = _inline_runs(chunk)
        if runs:
            blocks.append({"type": "paragraph", "content": runs})
    return blocks


def _depths(tree: ParsedTree) -> dict[str, int]:
    """Tree depth per node id, from the parent_id chain (cycle-safe)."""
    by_id = {n.id: n for n in tree.nodes if n.id}
    cache: dict[str, int] = {}

    def depth(nid: str, seen: frozenset[str]) -> int:
        if nid in cache:
            return cache[nid]
        node = by_id.get(nid)
        pid = node.parent_id if node else None
        d = 0 if (not pid or pid not in by_id or pid in seen) else depth(pid, seen | {nid}) + 1
        cache[nid] = d
        return d

    for nid in by_id:
        depth(nid, frozenset())
    return cache


def build_doc(tree: ParsedTree) -> dict:
    """Render a :class:`ParsedTree` into a ProseMirror ``doc`` node (``tree.doc.json``)."""
    depths = _depths(tree)
    content: list[dict] = []
    for node in tree.nodes:
        if node.retired:
            continue
        level = depths.get(node.id or "", 0)
        heading: dict = {
            "type": "featureHeading",
            "attrs": {"fid": node.id, "level": level, "realized": node.realized is not False},
        }
        title = (node.title or "").strip()
        if title:
            heading["content"] = [{"type": "text", "text": title}]
        content.append(heading)
        content.extend(_paragraphs(node.description))
    return {"type": "doc", "content": content}


def build_doc_from_text(text: str) -> dict:
    """Parse ``tree.codoc`` text and render it to the ``tree.doc.json`` shape."""
    return build_doc(parse_text(text))
