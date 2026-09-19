"""Review-8 probes for the hub (`codoc/serve/*`) and the transport, at 18855c0.
Every test asserts the CORRECT behaviour: a failing test is a confirmed fault,
a passing one (marked "confirm") a confirmed property.

From the worktree:

    P=../CodeNav/.venv/bin/python; S=<scratchpad>
    PYTHONPATH=. $P -m pytest $S/probe8_hub.py -q -p no:cacheprovider
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codoc.calculus import ops as K
from codoc.doclang import write_config
from codoc.serve.app import build_app, client_name
from codoc.serve.auth import MAINTAINER, NONE, SUGGEST, record_role
from codoc.serve.push import Feed, event_source
from codoc.serve.sandbox import can_use_tool
from codoc.substrate import log as L
from codoc.substrate.log import Submission
from codoc.substrate.publish import publish
from codoc.substrate.workspace import Workspace
from tests.loops.test_reflect import seed

H = {"x-codoc-csrf": "1"}


def workspace(tmp_path, roles=None):
    cd = tmp_path / ".codoc"
    cd.mkdir(parents=True, exist_ok=True)
    if roles:
        write_config(cd, roles=roles)
    ws = Workspace(cd)
    seed(ws)
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    return cd


def edit_and_publish(tmp_path, cd, text=" More."):
    ws = Workspace(cd)
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": text}}], actor="human:local")
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()


# ── the sandbox ──────────────────────────────────────────────────────────────

def test_H1_a_grep_for_a_path_like_literal_is_a_regex_not_a_path_and_is_allowed(tmp_path):
    """Round 7 confined `Glob.pattern` (an absolute pattern lists files anywhere).
    `can_use_tool` reads `pattern` for EVERY tool, and `Grep`'s `pattern` is the
    regex: an agent searching the worktree for the route `/api/message` — or
    any literal that starts with a slash — is refused as "outside the worktree"."""
    wt = tmp_path / "wt"
    wt.mkdir()
    ok, why = can_use_tool("Grep", {"pattern": "/api/message"}, scope=["a.py"], root=str(wt))
    assert ok, why
    ok, why = can_use_tool("Grep", {"pattern": "/healthz", "path": str(wt / "codoc")}, scope=["a.py"], root=str(wt))
    assert ok, why


def test_H2_a_relative_glob_pattern_that_climbs_out_of_the_worktree_is_confined(tmp_path):
    """Only an absolute or `~` pattern is checked; a relative one is resolved by
    the tool against its cwd (the worktree) and `../..` walks out of it. Names
    only, as in round 7's A10 — but the same boundary."""
    wt = tmp_path / "a" / "b" / "wt"
    wt.mkdir(parents=True)
    ok, _ = can_use_tool("Glob", {"pattern": "../../*"}, scope=["a.py"], root=str(wt))
    assert ok is False
    ok, _ = can_use_tool("Glob", {"pattern": "../../../*/.ssh/*"}, scope=["a.py"], root=str(wt))
    assert ok is False
    ok, why = can_use_tool("Glob", {"pattern": "src/**/*.py"}, scope=["a.py"], root=str(wt))
    assert ok, why


# ── the hub's session / stream lifecycle ─────────────────────────────────────

def test_H3_a_revoked_collaborators_open_stream_stops_carrying_the_tree(tmp_path):
    """`/api/events` checks the session once, at connect, and hands
    `event_source` only `request.is_disconnected`. A collaborator taken off the
    repository keeps receiving every projection for as long as their
    EventSource stays open (hours; the browser reconnects it too). Their
    WRITES are refused at the next POST; their READ of the live tree is not."""
    cd = workspace(tmp_path, roles={"human:gh:eve": SUGGEST})

    async def run():
        async def connected():
            return False
        gen = event_source(cd, client_name("eve"), is_disconnected=connected, poll_s=0.01)
        first = [(await asyncio.wait_for(gen.__anext__(), 2))["event"] for _ in range(2)]
        assert first == ["projection", "status"]
        record_role(cd, "eve", NONE)                       # the ten-minute recheck found her removed
        edit_and_publish(tmp_path, cd)                     # the tree moves on
        try:
            nxt = await asyncio.wait_for(gen.__anext__(), 2)
        except StopAsyncIteration:
            return None
        return nxt["event"]

    got = asyncio.run(run())
    assert got is None or got != "projection", f"the stream still delivered a {got} after revocation"


def test_H4_confirm_a_connected_viewer_keeps_receiving_acks_across_the_daemons_trim(tmp_path):
    cd = workspace(tmp_path)
    for i in range(2100):
        L.ack(cd, "hub-alice", f"c{i}", None)
    feed = Feed(cd, "hub-alice")
    feed.pending()
    L.rotate_acks(cd, keep=2000)
    L.ack(cd, "hub-alice", "fresh", None)
    got = [a["cid"] for m in feed.pending() if m["kind"] == "acks" for a in m["acks"]]
    assert "fresh" in got


def test_H5_confirm_the_feed_delivers_acks_before_the_projection_that_followed_them(tmp_path):
    cd = workspace(tmp_path)
    feed = Feed(cd, "ide")
    feed.pending()
    ws = Workspace(cd)
    t = ws.store.blocks["p1"].text()
    L.submit(cd, "ide", [Submission(cid="k1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                    payload={"after": list(t.last_live_ref()), "text": " More."}, batch="b1")])
    ws.drain_inbox()
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    kinds = [m["kind"] for m in feed.pending()]
    assert kinds.index("acks") < kinds.index("projection"), kinds


def test_H6_a_reconnect_while_the_daemon_is_mid_write_still_replays_the_gaps_acks(tmp_path):
    """`Feed.__init__` replays the newest acks so an EventSource reconnect does
    not lose the ack written in the gap. The offset is computed as
    `size - bytes(last N complete lines)`, which assumes the file ENDS with a
    newline: when it ends in a half-written line, the offset lands at the start
    of the torn tail and every complete line before it is skipped — the very
    acks the replay exists to deliver. The blocks they released stay held for
    the page's life, and ⌘Z waits on them for ever."""
    cd = workspace(tmp_path)
    for i in range(3):
        L.ack(cd, "hub-alice", f"gap{i}", None)
    path = L.inbox_dir(cd) / "hub-alice.ack.jsonl"
    with path.open("ab") as fh:
        fh.write(b'{"cid": "half')                         # the daemon is between two writes
    feed = Feed(cd, "hub-alice")
    got = [a["cid"] for m in feed.pending() if m["kind"] == "acks" for a in m["acks"]]
    assert got == ["gap0", "gap1", "gap2"], got


# ── authority: the applier's rule and the margin's ───────────────────────────

def test_H7_confirm_a_contributor_who_started_a_thread_resolves_it_after_a_maintainer_replied(tmp_path):
    """The margin offers Resolve to the thread's STARTER (comments.ts:196) and
    sends `block.set(status)` on every block of the thread, replies included.
    The applier judges each by the thread's minter (`_own_comment`), so the two
    agree: every op lands. A starter-vs-minter mismatch on either side would
    half-resolve the thread."""
    cd = workspace(tmp_path, roles={"human:gh:alice": SUGGEST, "human:gh:bob": MAINTAINER})
    ws = Workspace(cd)
    L.submit(cd, client_name("alice"), [
        Submission(cid="a1", kind=K.BLOCK_ADD, target={"bid": "c1"},
                   payload={"kind": "comment", "after": "p1", "content": {"thread": "c1", "anchor": "p1", "anchor_text": "Retries", "scope": "code", "status": "open"}}, batch="b1"),
        Submission(cid="a2", kind=K.TEXT_INSERT, target={"bid": "c1", "path": "body"}, payload={"after": "START", "text": "split this"}, batch="b1"),
    ])
    L.submit(cd, client_name("bob"), [
        Submission(cid="b1", kind=K.BLOCK_ADD, target={"bid": "c2"},
                   payload={"kind": "comment", "after": "c1", "content": {"thread": "c1", "anchor": "p1", "anchor_text": "Retries", "scope": "code", "status": "open"}}, batch="b2"),
        Submission(cid="b2", kind=K.TEXT_INSERT, target={"bid": "c2", "path": "body"}, payload={"after": "START", "text": "agreed"}, batch="b2"),
    ])
    assert [o.conflict for o in ws.drain_inbox()] == [None, None, None, None]
    L.submit(cd, client_name("alice"), [
        Submission(cid="a3", kind=K.BLOCK_SET, target={"bid": "c1", "path": "status"}, payload={"value": "resolved"}, batch="b3"),
        Submission(cid="a4", kind=K.BLOCK_SET, target={"bid": "c2", "path": "status"}, payload={"value": "resolved"}, batch="b3"),
    ])
    assert [o.conflict for o in ws.drain_inbox()] == [None, None]
    assert ws.store.blocks["c1"].content["status"] == ws.store.blocks["c2"].content["status"] == "resolved"
    ws.close()


def test_H8_confirm_a_revoked_collaborator_recorded_as_none_is_denied_by_the_applier(tmp_path):
    cd = workspace(tmp_path, roles={"human:gh:eve": NONE})
    ws = Workspace(cd)
    L.submit(cd, client_name("eve"), [
        Submission(cid="e1", kind=K.PLAN_OPEN, target={"pid": "pl-eve"}, payload={"kind": "edit", "target": "p1", "note": "hi"}, batch="pl-eve"),
        Submission(cid="e2", kind=K.BLOCK_ADD, target={"bid": "ce"},
                   payload={"kind": "comment", "after": "p1", "content": {"thread": "ce", "anchor": "p1", "anchor_text": "", "scope": "code", "status": "open"}}, batch="b"),
    ])
    assert all(o.conflict == "authority" for o in ws.drain_inbox())
    ws.close()


# ── the write path ───────────────────────────────────────────────────────────

def test_H9_one_message_cannot_carry_an_unbounded_number_of_submissions(tmp_path):
    """The rate limit is per REQUEST (60 burst, 5/s). One request may carry any
    number of submissions and any size of text; every one is appended and
    drained, and a suggester's batch opens a plan each. A cap on `len(subs)`
    (and on the body) is the missing half of the limit."""
    cd = workspace(tmp_path)
    c = TestClient(build_app(str(cd)))
    subs = [{"cid": f"c{i}", "kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
             "payload": {"after": "START", "text": "x" * 100}, "batch": f"b{i}"} for i in range(5000)]
    r = c.post("/api/message", headers=H, json={"kind": "submit", "subs": subs})
    assert r.status_code in (400, 413), (r.status_code, r.json())


# ── contract facts the editor's probes lean on ───────────────────────────────

def test_H10_accepting_an_insert_only_plan_moves_the_blocks_head(tmp_path):
    """`adoptDirty` (editor.ts) repaints a block only when its projected `head`
    moved or its feature's phase/claims/watermarks changed. A verdict on a plan
    that only inserted changes what the block's words are painted (open → accepted)
    without touching the block: `store.record` bumps `plans_head`, not
    `block_head`. If this holds, probe B3's stale gray is reachable from the daemon."""
    cd = workspace(tmp_path)
    ws = Workspace(cd)
    pid = ws.open_plan(actor="agent:claude-code", kind="edit", target="p1")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " (proposed)"}}], actor="agent:claude-code", plan=pid)
    before = ws.store.block_head["p1"]
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:local")
    after = ws.store.block_head["p1"]
    ws.close()
    assert after > before, f"block_head unchanged by the accept ({before}); the editor cannot see the verdict on this block's paints"


def test_H11_confirm_typing_into_a_block_another_actor_deleted_is_refused_so_the_editor_takes_the_conflict_path(tmp_path):
    """Grounds the editor's `caughtUp` skipping a released block the projection
    no longer has: the op that released it was refused (`dead_block`), so the
    conflict re-adoption removes the block; there is no clean ack on a gone block."""
    cd = workspace(tmp_path, roles={"human:gh:bob": MAINTAINER})
    ws = Workspace(cd)
    t = ws.store.blocks["p1"].text()
    anchor = list(t.last_live_ref())
    ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "p1"}, "payload": {}}], actor="human:local")
    L.submit(cd, client_name("bob"), [Submission(cid="b1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                                   payload={"after": anchor, "text": " late"}, batch="b")])
    ops = ws.drain_inbox()
    assert [o.conflict for o in ops] == ["dead_block"]
    ack = json.loads((L.inbox_dir(cd) / f"{client_name('bob')}.ack.jsonl").read_text().splitlines()[-1])
    assert ack["conflict"] == "dead_block" and ack["op"]
    ws.close()
