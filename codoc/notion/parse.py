"""Parse a Notion page's block tree into a :class:`ParsedTree`.

This is the Notion analogue of :mod:`codoc.codoc_file.doc_parse` (the
``tree.doc.json`` walker): it produces the SAME ``ParsedTree`` shape, so
``diff_codoc`` and the rest of Loop B are reused unchanged.

Representation (see the plan's U2/U3 decision): each feature is a **toggle block**,
nested by block parent/child — toggles nest to arbitrary depth and round-trip
cleanly, where Notion headings cap at three levels and would break the echo-loop
guard for deeper trees. A toggle's non-toggle children are its own content
(paragraphs → description prose, quotes → steering comments); its toggle children
are its sub-features. Callout blocks are display-only proposals and are skipped
here (verdicts arrive via comments → ``.codoc/inbox.json``).

Identity: a Notion block id is assigned by Notion, so the bridge maintains a
``block_id → feature_id`` map (``notion_map.json``, owned by the bridge — U9). This
parser takes that map: a known block resolves to its feature id; an unknown block
is a genuine ADD carrying ``local_id = block_id`` so the minted feature id can be
mapped back. The map is not user-editable, so it is an authoritative identity
source — unlike a title, which a user can rename.

Input contract: ``parse_blocks`` expects the page's top-level block list with each
block's children **already hydrated** under a ``"children"`` key (the client walks
``GET /v1/blocks/{id}/children`` recursively before calling this pure function).
"""
from __future__ import annotations

from codoc.codoc_file.parse import (
    ParsedNode, ParsedTree, extract_refs, normalize_description,
)

# Toggle = a feature; paragraph = prose; quote = steering. Callout = a proposal
# decoration (display-only). Everything else is ignored on the parse side.
_TOGGLE = "toggle"
_PARAGRAPH = "paragraph"
_QUOTE = "quote"


def rich_text_to_markdown(rich: list | None) -> str:
    """Project a Notion ``rich_text`` array back to the markdown the store holds.

    Inverse of the render tokenizer (U2): a bold run → ``**…**``; an https link run
    → ``[label](url)``; a plain run is emitted verbatim (so an authored
    ``[label](codoc:file#sym)`` citation — which renders as literal text, not a
    Notion link, to stay scheme-safe and round-trip exactly — comes back unchanged).
    """
    parts: list[str] = []
    for r in rich or []:
        if not isinstance(r, dict):
            continue
        text_obj = r.get("text") or {}
        content = text_obj.get("content")
        if content is None:
            content = r.get("plain_text") or ""
        href = r.get("href") or (text_obj.get("link") or {}).get("url")
        if href and href.startswith(("http://", "https://")):
            seg = f"[{content}]({href})"
        else:
            seg = content
        ann = r.get("annotations") or {}
        if ann.get("bold") and seg:
            seg = f"**{seg}**"
        parts.append(seg)
    return "".join(parts)


def _block_type(block: dict) -> str | None:
    t = block.get("type")
    return t if isinstance(t, str) else None


def _rich_of(block: dict, kind: str) -> list:
    payload = block.get(kind)
    if isinstance(payload, dict):
        rt = payload.get("rich_text")
        if isinstance(rt, list):
            return rt
    return []


def _description_from(paras: list[dict]) -> str:
    """Paragraph blocks → one description string, matching ``doc_parse._description``:
    drop empties, join with a blank line, then apply the canonical normalization
    (R19) so a Notion round-trip never produces a phantom AMEND."""
    texts = [t for t in (rich_text_to_markdown(_rich_of(p, _PARAGRAPH)) for p in paras)
             if t.strip()]
    return normalize_description("\n\n".join(texts))


def parse_blocks(blocks: list[dict], block_to_fid: dict[str, str] | None = None) -> ParsedTree:
    """Walk a hydrated Notion block tree → ParsedTree (pure; no I/O).

    ``block_to_fid`` maps a Notion block id to its codoc feature id; pass the
    bridge's persisted map. Absent → every toggle is treated as a new node.
    """
    id_map = block_to_fid or {}
    tree = ParsedTree()

    def walk(siblings: list, parent_fid: str | None) -> None:
        for b in siblings or []:
            if not isinstance(b, dict) or _block_type(b) != _TOGGLE:
                continue
            block_id = b.get("id") or ""
            title = rich_text_to_markdown(_rich_of(b, _TOGGLE)).strip()
            children = b.get("children") or []
            paras = [c for c in children if isinstance(c, dict) and _block_type(c) == _PARAGRAPH]
            quotes = [c for c in children if isinstance(c, dict) and _block_type(c) == _QUOTE]
            sub_toggles = [c for c in children if isinstance(c, dict) and _block_type(c) == _TOGGLE]

            description = _description_from(paras)
            fid = id_map.get(block_id)
            node = ParsedNode(
                id=fid,
                title=title,
                description=description,
                parent_id=parent_fid,
                retired=False,  # retiring is via the proposal flow, never by deletion
                # Known block → identity comes from the authoritative map (via fid),
                # so local_id is left empty. Unknown block → a genuine ADD carrying
                # the block id as local_id, so the minted feature id maps back.
                local_id="" if fid else block_id,
                realized=None,
            )
            node.refs = extract_refs(description)
            for q in quotes:
                text = rich_text_to_markdown(_rich_of(q, _QUOTE)).strip()
                if text:
                    node.comments.append(text)
            tree.nodes.append(node)
            # Recurse: a child feature's parent is THIS node's feature id (or None
            # when this node is itself new — mirrors doc_parse's stack behavior).
            walk(sub_toggles, fid)

    walk(blocks, None)
    return tree
