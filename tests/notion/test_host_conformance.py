"""U11 — Notion host conformance: tree-parity (the host contract's core) and the
explicit typed-media deferral, validated against the shared conformance harness.
"""
from __future__ import annotations

from codoc.blocks.conformance import canonical_block_view
from codoc.codoc_file.diff import diff_codoc
from codoc.model.feature import Feature
from codoc.notion.parse import parse_blocks
from codoc.notion.render import render_blocks
from codoc.store.db import open_store


def _maps(store):
    f2b, b2f = {}, {}
    for f in store.list_features():
        f2b[f.id] = f"blk-{f.id}"
        b2f[f"blk-{f.id}"] = f.id
    return f2b, b2f


def test_tree_parity_is_exact(tmp_path):
    """The host contract's core: render → parse → diff is empty (the host reproduces
    the protocol's tree faithfully, neither dropping nor mistyping a feature)."""
    cd = tmp_path / ".codoc"; cd.mkdir()
    with open_store(cd) as s:
        p = Feature(title="Auth", description="Login + **sessions**.")
        s.upsert_feature(p)
        s.upsert_feature(Feature(title="Tokens", description="JWT.", parent_id=p.id))
        s.upsert_feature(Feature(title="Billing", description="Cites [c](codoc:c.py#f)."))
        f2b, b2f = _maps(s)
        blocks = render_blocks(s, fid_to_block=f2b)
        assert diff_codoc(parse_blocks(blocks, b2f), s, has_local_ids=True).is_empty()


def test_render_is_prose_only_typed_media_deferred(tmp_path):
    """v1 documented limitation: the Notion host renders prose (toggles + paragraphs),
    not typed-media blocks (diagram/screenshot). canonical_block_view would report
    such media; the Notion render emits only toggle/paragraph block types. When media
    rendering is added it MUST match canonical_block_view (the parity contract)."""
    cd = tmp_path / ".codoc"; cd.mkdir()
    with open_store(cd) as s:
        s.upsert_feature(Feature(title="Auth", description="prose"))
        blocks = render_blocks(s)

    def _types(bs):
        for b in bs:
            yield b["type"]
            yield from _types(b.get("children") or [])

    assert set(_types(blocks)) <= {"toggle", "paragraph"}


def test_canonical_block_view_available_for_future_media_parity():
    # The harness the Notion media renderer will be validated against, when added.
    assert canonical_block_view({"version": 5, "features": {}}) == {}
