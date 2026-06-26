"""U9 — bridge inbound cycle, identity map, ownership (fake client + temp store)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codoc.loop import edits as edits_channel
from codoc.loop import inbox
from codoc.loop.loop_b import run_loop_b
from codoc.model.feature import Feature
from codoc.notion.bridge import (
    BridgeOwnershipError, NotionMap, collect_verdicts, ensure_daemon_owner,
    iter_blocks, push_outbound, reconcile_inbound,
)
from codoc.notion.config import NotionConfig
from codoc.notion.push import BlockPushStream
from codoc.notion.render import proposal_callout_block, render_blocks
from codoc.store.db import open_store

_CFG = NotionConfig(token="t", page_id="page-root")


@pytest.fixture
def dirs(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    cd = tmp_path / ".codoc"; cd.mkdir()
    return str(root), str(cd)


class FakeClient:
    def __init__(self, blocks=None, comments=None):
        self._blocks = blocks or []
        self._comments = comments or {}  # block_id -> [comment dicts]
        self.writes = []

    def get_block_tree(self):
        return self._blocks

    def list_comments(self, block_id):
        return self._comments.get(block_id, [])

    def write_page_tree(self, fid_to_block, blocks):
        self.writes.append(blocks)
        return {"ok": True}


def _rt(content):
    return {"type": "text", "text": {"content": content}, "annotations": {"bold": False}}


def _toggle(block_id, title, *, children=None):
    return {"id": block_id, "type": "toggle", "has_children": bool(children),
            "toggle": {"rich_text": [_rt(title)]}, "children": children or []}


def _para(text):
    return {"type": "paragraph", "paragraph": {"rich_text": [_rt(text)]}}


# ── ownership ────────────────────────────────────────────────────────────────

def test_ensure_owner_raises_without_daemon(dirs):
    _root, cd = dirs
    with pytest.raises(BridgeOwnershipError):
        ensure_daemon_owner(cd)


def test_ensure_owner_ok_with_live_pidfile(dirs, monkeypatch):
    _root, cd = dirs
    monkeypatch.setattr("codoc.loop.watch.daemon_running", lambda d: True)
    ensure_daemon_owner(cd)  # no raise


# ── NotionMap ────────────────────────────────────────────────────────────────

def test_notion_map_roundtrip_and_invert(dirs):
    _root, cd = dirs
    m = NotionMap(cd)
    m.set("f-1", "blk-1")
    m.save()
    again = NotionMap(cd)
    assert again.fid_to_block() == {"f-1": "blk-1"}
    assert again.block_to_fid() == {"blk-1": "f-1"}


def test_notion_map_learns_minted_features(dirs):
    _root, cd = dirs
    with open_store(cd) as s:
        f = Feature(title="New", description="d", local_id="blk-new")
        s.upsert_feature(f)
        m = NotionMap(cd)
        learned = m.learn_from_store(s, {"blk-new", "blk-other"})
        assert learned == 1
        assert m.fid_to_block()[f.id] == "blk-new"


# ── iter_blocks / collect_verdicts ───────────────────────────────────────────

def test_iter_blocks_walks_nested():
    tree = [_toggle("a", "A", children=[_para("x"), _toggle("b", "B")])]
    ids = [b.get("id") for b in iter_blocks(tree) if b.get("id")]
    assert ids == ["a", "b"]


def test_collect_verdicts_reads_callout_comments():
    callout = {**proposal_callout_block("e-1", "add", "X"), "id": "callout-blk"}
    client = FakeClient(blocks=[callout],
                        comments={"callout-blk": [{"rich_text": [_rt("/accept")]}]})
    found = collect_verdicts(client, [callout])
    assert found == [("callout-blk", "e-1", "/accept")]


# ── inbound cycle ────────────────────────────────────────────────────────────

def test_reconcile_inbound_dispatches_edits(dirs):
    root, cd = dirs
    with open_store(cd) as s:
        f = Feature(title="Auth", description="old")
        s.upsert_feature(s_f := f)
        fid = f.id
    blocks = [_toggle(f"blk-{fid}", "Auth", children=[_para("new prose")])]
    m = NotionMap(cd); m.set(fid, f"blk-{fid}"); m.save()

    client = FakeClient(blocks=blocks)
    with open_store(cd) as store:
        res = reconcile_inbound(cd, store, client, m)
    assert res.node_ops == 1
    # the node_ops channel now carries the amend
    assert len(edits_channel.read_node_ops(cd)) == 1


def test_reconcile_inbound_applies_verdict(dirs):
    root, cd = dirs
    # a pending proposal in the store
    from codoc.model.event import Event, NodeOp, NodeOpKind
    with open_store(cd) as s:
        e = Event(source="loop_a", applied=False,
                  op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Theme", realized=False,
                            description="d", rationale="planned"))
        s.append_event(e)
        event_id = e.id

    callout = {**proposal_callout_block(event_id, "add", "Theme"), "id": "c1"}
    client = FakeClient(blocks=[callout],
                        comments={"c1": [{"rich_text": [_rt("/accept")]}]})
    with open_store(cd) as store:
        reconcile_inbound(cd, store, client, NotionMap(cd))
    # the verdict landed in the inbox
    verdicts = inbox.read_verdicts(cd)
    assert any(v.event_id == event_id and v.accept for v in verdicts)


def test_reconcile_inbound_learns_minted_identity(dirs):
    root, cd = dirs
    with open_store(cd):
        pass
    blocks = [_toggle("blk-new", "Brand new", children=[_para("fresh")])]
    client = FakeClient(blocks=blocks)
    # dispatch the ADD, then run Loop B to mint the feature with local_id=blk-new
    with open_store(cd) as store:
        reconcile_inbound(cd, store, client, NotionMap(cd))
    run_loop_b(root, cd, dry_run=False)
    # a second inbound cycle learns the fid->block mapping from the minted feature
    m = NotionMap(cd)
    with open_store(cd) as store:
        reconcile_inbound(cd, store, client, m)
    assert "blk-new" in m.fid_to_block().values()


# ── outbound push ────────────────────────────────────────────────────────────

def test_push_outbound_writes_on_change_and_skips_noop(dirs):
    _root, cd = dirs
    with open_store(cd) as s:
        s.upsert_feature(Feature(title="Auth", description="d"))
    client = FakeClient()
    with open_store(cd) as store:
        stream = BlockPushStream(lambda: render_blocks(store, fid_to_block=NotionMap(cd).fid_to_block()))
        assert push_outbound(cd, store, client, NotionMap(cd), stream) is True
        assert push_outbound(cd, store, client, NotionMap(cd), stream) is False  # no-op
    assert len(client.writes) == 1
