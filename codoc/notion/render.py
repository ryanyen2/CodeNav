"""Render the feature tree to a Notion block tree (the authoring surface).

The inverse of :mod:`codoc.notion.parse`. Each feature becomes a **toggle block**
nested by parent/child; its description becomes paragraph children (one per
paragraph), tokenized so ``**bold**`` rides as a bold annotation and
``[label](https://…)`` rides as a real Notion link. ``codoc:`` citations render as
**literal text** (scheme-safe, and so the parse round-trip is exact) — they are not
clickable repo navigation in v1.

This reads the tree + prose from the **store** (like ``codoc_file/render.py`` renders
``tree.codoc``), not from the sidecar: the sidecar carries only a one-line ``pitch``,
not the full description an authoring surface must show. The bridge holds a read-only
Store handle anyway (``diff_codoc`` needs one), so this is consistent. Proposal
callouts + verdict affordances are layered on in U6.

Block identity: a feature already mirrored to Notion has a block id in the bridge's
``fid_to_block`` map; render stamps it as the toggle's ``id`` so the client updates
the existing block. A feature with no mapping yet is emitted without an ``id`` (the
client creates it and records the assigned id).
"""
from __future__ import annotations

import re

from codoc.store.db import Store

# A bold span or an https link — the two markdown constructs that become structured
# Notion rich-text runs. A codoc: citation is deliberately NOT matched here (its
# scheme is not http/https), so it stays literal text and round-trips verbatim.
_TOKEN_RE = re.compile(
    r"\*\*(?P<bold>[^*\n]+)\*\*"
    r"|\[(?P<label>[^\]]*)\]\((?P<url>https?://[^)\s]+)\)"
)


def _text_run(content: str, *, bold: bool = False, link: str | None = None) -> dict:
    return {
        "type": "text",
        "text": {"content": content, "link": ({"url": link} if link else None)},
        "annotations": {"bold": bold},
    }


def text_to_rich(text: str) -> list[dict]:
    """Tokenize a markdown paragraph into Notion rich-text runs (inverse of
    :func:`codoc.notion.parse.rich_text_to_markdown`)."""
    runs: list[dict] = []
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        if m.start() > pos:
            runs.append(_text_run(text[pos:m.start()]))
        if m.group("bold") is not None:
            runs.append(_text_run(m.group("bold"), bold=True))
        else:
            runs.append(_text_run(m.group("label"), link=m.group("url")))
        pos = m.end()
    if pos < len(text):
        runs.append(_text_run(text[pos:]))
    return runs or [_text_run("")]


def _paragraph(text: str) -> dict:
    return {"type": "paragraph", "paragraph": {"rich_text": text_to_rich(text)}}


def _description_paragraphs(description: str) -> list[dict]:
    """Split a description into paragraph blocks on blank-line boundaries, matching
    how :func:`codoc.notion.parse._description_from` rejoins them with ``\\n\\n``."""
    if not description:
        return []
    return [_paragraph(p) for p in description.split("\n\n") if p.strip()]


def render_blocks(store: Store, *, fid_to_block: dict[str, str] | None = None) -> list[dict]:
    """Render live features → a nested Notion toggle-block tree.

    Siblings and roots are ordered by lowercased title (matching ``serve/payload``)
    so the page renders deterministically and a no-op render is byte-stable.
    """
    block_map = fid_to_block or {}
    features = store.list_features()  # live only
    by_id = {f.id: f for f in features}

    children: dict[str | None, list] = {}
    for f in features:
        pid = f.parent_id if f.parent_id in by_id else None
        children.setdefault(pid, []).append(f)
    for sibs in children.values():
        sibs.sort(key=lambda f: (f.title or "").lower())

    seen: set[str] = set()

    def toggle(f) -> dict:
        seen.add(f.id)
        block: dict = {
            "type": "toggle",
            "toggle": {"rich_text": text_to_rich(f.title or "")},
        }
        bid = block_map.get(f.id)
        if bid:
            block["id"] = bid
        kids: list[dict] = list(_description_paragraphs(f.description or ""))
        for child in children.get(f.id, []):
            if child.id not in seen:  # cycle guard
                kids.append(toggle(child))
        block["children"] = kids
        block["has_children"] = bool(kids)
        return block

    return [toggle(r) for r in children.get(None, [])]
