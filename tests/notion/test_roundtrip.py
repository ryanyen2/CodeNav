"""U4 — round-trip idempotency + push dedup (the echo-loop guard).

The core safety property for a live two-way sync: render(store) → Notion blocks →
parse → diff_codoc must be EMPTY, and an unchanged render must not push. Without
this, a Notion edit echoes forever (edit → store → push-back → re-detected).
"""
from __future__ import annotations

from codoc.codoc_file.diff import diff_codoc
from codoc.model.feature import Feature
from codoc.notion.parse import parse_blocks
from codoc.notion.push import BlockPushStream
from codoc.notion.render import render_blocks
from codoc.store.db import open_store


def _consistent_maps(store):
    """Build inverse fid<->block maps so a rendered tree re-parses to the same fids."""
    fid_to_block, block_to_fid = {}, {}
    for f in store.list_features():
        bid = f"blk-{f.id}"
        fid_to_block[f.id] = bid
        block_to_fid[bid] = f.id
    return fid_to_block, block_to_fid


def _seed(store):
    p = Feature(title="Auth", description="Login and **sessions**.")
    store.upsert_feature(p)
    c = Feature(title="Tokens", description="JWT issue + verify. See [rfc](https://rfc.example).",
                parent_id=p.id)
    store.upsert_feature(c)
    store.upsert_feature(Feature(title="Billing", description="Binds [pay](codoc:pay.py#charge)."))
    return p, c


def test_render_parse_roundtrip_is_empty(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    with open_store(cd) as s:
        _seed(s)
        fid_to_block, block_to_fid = _consistent_maps(s)
        blocks = render_blocks(s, fid_to_block=fid_to_block)
        diff = diff_codoc(parse_blocks(blocks, block_to_fid), s, has_local_ids=True)
        assert diff.is_empty(), [op.kind for op in diff.user_ops]


def test_roundtrip_empty_with_nested_and_markup(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    with open_store(cd) as s:
        # multi-paragraph, bold, https link, codoc citation, nesting — all must survive.
        p = Feature(title="Parent", description="Para one.\n\nPara **two** with [x](https://y.z).")
        s.upsert_feature(p)
        s.upsert_feature(Feature(title="Child", description="Cites [c](codoc:c.py#f).", parent_id=p.id))
        fid_to_block, block_to_fid = _consistent_maps(s)
        blocks = render_blocks(s, fid_to_block=fid_to_block)
        assert diff_codoc(parse_blocks(blocks, block_to_fid), s, has_local_ids=True).is_empty()


def test_push_stream_suppresses_identical_render(tmp_path):
    cd = tmp_path / ".codoc"; cd.mkdir()
    with open_store(cd) as s:
        _seed(s)
        fid_to_block, _ = _consistent_maps(s)
        stream = BlockPushStream(lambda: render_blocks(s, fid_to_block=fid_to_block))
        first = stream.next_if_changed()
        assert first is not None  # cold push
        assert stream.next_if_changed() is None  # identical → suppressed


def test_push_stream_emits_on_content_change():
    # Drive the render callable directly: same content suppresses, changed content pushes.
    state = {"blocks": [{"type": "toggle", "toggle": {"rich_text": []}}]}
    stream = BlockPushStream(lambda: state["blocks"])
    assert stream.next_if_changed() is not None
    assert stream.next_if_changed() is None  # HLC may advance, content same → suppressed
    state["blocks"] = [{"type": "toggle", "toggle": {"rich_text": [{"text": {"content": "x"}}]}}]
    assert stream.next_if_changed() is not None  # content changed → push


def test_amend_in_notion_then_roundtrip_converges(tmp_path):
    """A real Notion edit applies once, then the next render→parse is a no-op (AE6-style)."""
    from codoc.loop.apply import apply_op

    cd = tmp_path / ".codoc"; cd.mkdir()
    with open_store(cd) as s:
        f = Feature(title="Auth", description="old")
        s.upsert_feature(f)
        fid_to_block, block_to_fid = _consistent_maps(s)
        # User edits the toggle's paragraph in Notion → parse → diff → apply.
        edited = [{"id": fid_to_block[f.id], "type": "toggle",
                   "toggle": {"rich_text": [{"type": "text", "text": {"content": "Auth"},
                                             "annotations": {"bold": False}}]},
                   "children": [{"type": "paragraph",
                                 "paragraph": {"rich_text": [{"type": "text",
                                               "text": {"content": "new prose"},
                                               "annotations": {"bold": False}}]}}]}]
        first = diff_codoc(parse_blocks(edited, block_to_fid), s, has_local_ids=True)
        assert not first.is_empty()
        for op in first.user_ops:
            apply_op(op, s, source="user", applied=True)
        # Re-render from the now-updated store and re-parse: converged, no echo.
        blocks = render_blocks(s, fid_to_block=fid_to_block)
        assert diff_codoc(parse_blocks(blocks, block_to_fid), s, has_local_ids=True).is_empty()
