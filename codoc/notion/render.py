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
from dataclasses import dataclass

from codoc.store.db import Store

# Event-id marker embedded in a proposal callout so a verdict comment can be tied
# back to its event. Reuses the existing ``⟨e-…⟩`` convention (codoc_file/parse.py).
_EVENT_ID_RE = re.compile(r"⟨(e-[0-9a-f]+)⟩")

# Human-readable lead-in per proposal kind (the callout's first words).
_PROPOSAL_LEAD = {
    "add": "Proposed new feature",
    "move": "Proposed move",
    "retire": "Proposed retire",
    "amend": "Proposed change",
}
# Emoji + color per kind, so the callout reads at a glance in Notion.
_PROPOSAL_STYLE = {
    "add": ("➕", "green_background"),
    "move": ("↪️", "blue_background"),
    "retire": ("🗑️", "red_background"),
    "amend": ("✏️", "yellow_background"),
}

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


# ── proposal callouts (U6) ───────────────────────────────────────────────────

@dataclass
class ProposalCallout:
    """A proposal rendered as a Notion callout, plus where to anchor it.

    ``anchor_feature_id`` is set for amend/retire (decorate the feature's toggle);
    ``anchor_parent_id`` is set for add/move (anchor under the destination parent,
    ``None`` = top level). The bridge inserts the ``block`` accordingly.
    """
    event_id: str
    op: str
    anchor_feature_id: str | None
    anchor_parent_id: str | None
    block: dict


def proposal_callout_block(event_id: str, op: str, summary: str) -> dict:
    """Build a callout carrying a recoverable ``⟨e-id⟩`` marker plus a human summary
    and the accept/reject command hint. No inline ✓/✗ exists in Notion, so the
    verdict is a comment command on this callout (degraded-surface trade-off)."""
    emoji, color = _PROPOSAL_STYLE.get(op, ("💡", "gray_background"))
    lead = _PROPOSAL_LEAD.get(op, "Proposal")
    text = f"{lead}: {summary}  ⟨{event_id}⟩\nComment /accept or /reject to decide."
    return {
        "type": "callout",
        "callout": {
            "rich_text": text_to_rich(text),
            "icon": {"type": "emoji", "emoji": emoji},
            "color": color,
        },
    }


def recover_event_id(callout: dict) -> str | None:
    """Pull the ``⟨e-id⟩`` back out of a proposal callout (inverse of the marker)."""
    if not isinstance(callout, dict) or callout.get("type") != "callout":
        return None
    from codoc.notion.parse import rich_text_to_markdown

    text = rich_text_to_markdown((callout.get("callout") or {}).get("rich_text"))
    m = _EVENT_ID_RE.search(text)
    return m.group(1) if m else None


def proposal_callouts(sidecar: dict) -> list[ProposalCallout]:
    """Build callouts for every pending proposal in the sidecar's ``proposals`` slice."""
    proposals = sidecar.get("proposals") or {}
    out: list[ProposalCallout] = []

    for fid, p in (proposals.get("by_feature") or {}).items():
        if not isinstance(p, dict):
            continue
        op = p.get("op") or ""
        summary = p.get("title") or p.get("rationale") or "(see tree)"
        out.append(ProposalCallout(
            event_id=p.get("event_id") or "", op=op,
            anchor_feature_id=fid, anchor_parent_id=None,
            block=proposal_callout_block(p.get("event_id") or "", op, summary)))

    for event_id, p in (proposals.get("by_event") or {}).items():
        if not isinstance(p, dict):
            continue
        op = p.get("op") or ""
        summary = p.get("title") or p.get("rationale") or "(see tree)"
        out.append(ProposalCallout(
            event_id=event_id, op=op,
            anchor_feature_id=None, anchor_parent_id=p.get("parent_id"),
            block=proposal_callout_block(event_id, op, summary)))

    return out
