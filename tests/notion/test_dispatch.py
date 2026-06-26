"""U5 — dispatch Notion edits into the channels, drained by Loop B (auto-handoff).

Exercises the real integration: dispatch writes node_ops/steers → run_loop_b applies
them and mints auto-handed-off directives. Plus the node_ops channel round-trip and
idempotency.
"""
from __future__ import annotations

import pytest

from codoc.loop import edits as edits_channel
from codoc.loop.loop_b import realize_path, run_loop_b
from codoc.model.event import NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.notion.dispatch import dispatch_notion_edits
from codoc.store.db import open_store


@pytest.fixture
def dirs(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    codoc_dir = tmp_path / ".codoc"; codoc_dir.mkdir()
    return str(root), str(codoc_dir)


def _rt(content):
    return {"type": "text", "text": {"content": content}, "annotations": {"bold": False}}


def _toggle(block_id, title, *, children=None):
    return {"id": block_id, "type": "toggle", "has_children": bool(children),
            "toggle": {"rich_text": [_rt(title)]}, "children": children or []}


def _para(text):
    return {"type": "paragraph", "paragraph": {"rich_text": [_rt(text)]}}


def _quote(text):
    return {"type": "quote", "quote": {"rich_text": [_rt(text)]}}


# ── node_ops channel round-trip ──────────────────────────────────────────────

def test_node_ops_channel_roundtrip(dirs):
    _root, cd = dirs
    ops = [
        NodeOp(kind=NodeOpKind.AMEND, feature_id="f-1", title="T", description="d"),
        NodeOp(kind=NodeOpKind.ADD_NODE, title="New", description="n", local_id="blk-x"),
    ]
    edits_channel.append_node_ops(cd, ops)
    read = edits_channel.read_node_ops(cd)
    assert [o.kind for o in read] == [NodeOpKind.AMEND, NodeOpKind.ADD_NODE]
    assert read[1].local_id == "blk-x"
    # drain is one-shot
    drained = edits_channel.drain_node_ops(cd)
    assert len(drained) == 2
    assert edits_channel.read_node_ops(cd) == []


def test_append_node_ops_preserves_sibling_lists(dirs):
    _root, cd = dirs
    edits_channel.append_steer(cd, edits_channel.Steer(feature_id="f-1", text="note"))
    edits_channel.append_node_ops(cd, [NodeOp(kind=NodeOpKind.AMEND, feature_id="f-1", description="x")])
    # the steer survived the node_ops write
    assert len(edits_channel.read_steers(cd)) == 1
    assert len(edits_channel.read_node_ops(cd)) == 1


# ── dispatch + Loop B integration ────────────────────────────────────────────

def test_amend_from_notion_applies_and_auto_hands_off(dirs):
    root, cd = dirs
    s = open_store(cd)
    f = Feature(title="Auth", description="old prose")
    s.upsert_feature(f)
    s.close()

    blocks = [_toggle(f"blk-{f.id}", "Auth", children=[_para("new prose")])]
    s2 = open_store(cd)
    res = dispatch_notion_edits(cd, s2, blocks, {f"blk-{f.id}": f.id})
    s2.close()
    assert res.node_ops == 1

    run_res = run_loop_b(root, cd, dry_run=False)
    # the amend applied to the store
    s3 = open_store(cd)
    assert s3.get_feature(f.id).description == "new prose"
    s3.close()
    # a directive was minted AND handed off (no draft id → authoritative posture)
    manifest = edits_channel.read_manifest(cd)
    assert manifest, "expected a minted directive"
    assert all(d.handed_off for d in manifest)
    assert realize_path(cd).exists()  # handed-off → written to realize.md
    assert run_res.user_edits >= 1


def test_add_from_notion_creates_feature(dirs):
    root, cd = dirs
    s = open_store(cd); s.close()  # empty store

    blocks = [_toggle("blk-new", "Brand new", children=[_para("fresh intent")])]
    s2 = open_store(cd)
    res = dispatch_notion_edits(cd, s2, blocks, {})
    s2.close()
    assert res.node_ops == 1

    run_loop_b(root, cd, dry_run=False)
    s3 = open_store(cd)
    titles = [f.title for f in s3.list_features()]
    # the new feature exists and carries the Notion block id as its local_id (mint-back)
    new = next(f for f in s3.list_features() if f.title == "Brand new")
    assert new.local_id == "blk-new"
    s3.close()
    assert "Brand new" in titles


def test_steering_quote_becomes_steer_with_scoped_id(dirs):
    _root, cd = dirs
    s = open_store(cd)
    f = Feature(title="Auth", description="prose")
    s.upsert_feature(f)
    blocks = [_toggle(f"blk-{f.id}", "Auth", children=[_para("prose"), _quote("please add tests")])]
    res = dispatch_notion_edits(cd, s, blocks, {f"blk-{f.id}": f.id})
    s.close()
    assert res.steers == 1
    steers = edits_channel.read_steers(cd)
    assert steers[0].text == "please add tests"
    assert steers[0].comment_id.startswith(f"notion:{f.id}:")


def test_unchanged_page_dispatches_nothing(dirs):
    _root, cd = dirs
    s = open_store(cd)
    f = Feature(title="Auth", description="prose")
    s.upsert_feature(f)
    blocks = [_toggle(f"blk-{f.id}", "Auth", children=[_para("prose")])]
    res = dispatch_notion_edits(cd, s, blocks, {f"blk-{f.id}": f.id})
    s.close()
    assert res.node_ops == 0 and res.steers == 0
    assert edits_channel.read_node_ops(cd) == []


def test_two_identical_quotes_distinct_steer_ids(dirs):
    _root, cd = dirs
    s = open_store(cd)
    f = Feature(title="Auth", description="prose")
    s.upsert_feature(f)
    # same text on two features → distinct scoped ids (no (fid,text) collapse across features)
    g = Feature(title="Billing", description="b")
    s.upsert_feature(g)
    blocks = [
        _toggle(f"blk-{f.id}", "Auth", children=[_para("prose"), _quote("same note")]),
        _toggle(f"blk-{g.id}", "Billing", children=[_para("b"), _quote("same note")]),
    ]
    dispatch_notion_edits(cd, s, blocks, {f"blk-{f.id}": f.id, f"blk-{g.id}": g.id})
    s.close()
    ids = {st.comment_id for st in edits_channel.read_steers(cd)}
    assert len(ids) == 2
