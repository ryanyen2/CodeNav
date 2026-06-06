"""codoc MCP tools: agent-driven reflection through apply_op + write_tree."""
from __future__ import annotations

import json

import pytest

from codoc.mcp import tools
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def codoc_dir(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir()
    return str(cd)


def _seed(codoc_dir, **kw) -> Feature:
    s = open_store(codoc_dir)
    try:
        f = Feature(**kw)
        s.upsert_feature(f)
        return f
    finally:
        s.close()


def test_propose_add_is_a_pending_proposal(codoc_dir):
    res = tools.propose_add(codoc_dir, title="Query cache", description="caches", rationale="new")
    assert res["ok"] and res["applied"] is False
    s = open_store(codoc_dir)
    try:
        pend = s.pending_events()
        assert len(pend) == 1
        assert pend[0].source == "loop_a_agent"  # agent-driven reflection
        assert pend[0].op.title == "Query cache"
        # not yet a live feature
        assert all(f.title != "Query cache" for f in s.list_features())
    finally:
        s.close()


def test_attach_applies_immediately_and_writes_tree(codoc_dir):
    f = _seed(codoc_dir, title="Auth")
    res = tools.attach(codoc_dir, feature_id=f.id, binds=["auth.py::login"])
    assert res["ok"] and res["applied"] is True
    s = open_store(codoc_dir)
    try:
        binds = [b.symbol_path for b in s.bindings_for_feature(f.id)]
        assert binds == ["auth.py::login"]
    finally:
        s.close()
    # tree.codoc + sidecar were rendered
    sidecar = json.loads((__import__("pathlib").Path(codoc_dir) / "tree.bindings.json").read_text())
    assert sidecar["version"] == 3


def test_reflect_mixes_safe_and_structural(codoc_dir):
    f = _seed(codoc_dir, title="Auth")
    res = tools.reflect(codoc_dir, ops=[
        {"kind": "attach", "feature_id": f.id, "binds": ["auth.py::login"]},
        {"kind": "add_node", "title": "Token store", "description": "JWTs", "binds": ["tok.py::store"]},
    ], rationale="implemented login + token store")
    assert res["ok"]
    assert res["applied"] == 1   # the attach
    assert res["proposed"] == 1  # the add_node
    s = open_store(codoc_dir)
    try:
        assert len(s.pending_events()) == 1
        assert s.binding_at("auth.py", "auth.py::login") is not None
    finally:
        s.close()


def test_reflect_rejects_unknown_feature(codoc_dir):
    res = tools.reflect(codoc_dir, ops=[{"kind": "attach", "feature_id": "f-nope", "binds": ["a.py::x"]}])
    assert res["ok"]
    assert res["results"][0]["ok"] is False
    assert res["applied"] == 0 and res["proposed"] == 0


def test_plan_add_creates_unrealized_placeholder_on_accept(codoc_dir):
    from codoc.loop.apply import apply_op

    res = tools.plan_add(codoc_dir, title="Dark mode", description="UI theme")
    assert res["ok"] and res["applied"] is False
    s = open_store(codoc_dir)
    try:
        ev = s.pending_events()[0]
        assert ev.source == "plan"
        assert ev.op.realized is False
        # Simulate IDE accept (Loop B path): apply the op.
        apply_op(ev.op, s, source="user", applied=True)
        s.delete_event(ev.id)
        placeholder = next(f for f in s.list_features() if f.title == "Dark mode")
        assert placeholder.realized is False
    finally:
        s.close()

    # plan_status reports it as unrealized
    st = tools.plan_status(codoc_dir)
    assert st["all_realized"] is False
    assert any(p["title"] == "Dark mode" for p in st["unrealized"])


def test_server_guard_surfaces_structured_error_without_codoc_dir(monkeypatch):
    from codoc.mcp import server

    # With no .codoc reachable from cwd, the guard returns a structured error.
    monkeypatch.setattr(server, "_dir", lambda: None)
    cd, err = server._need_dir()
    assert cd is None
    assert err == {"ok": False, "error": "no .codoc directory found from cwd — run `codoc init` first"}


# ─── codoc_await_verdicts (blocking realization trigger) ──────────────────────

def test_await_verdicts_accept_makes_placeholder_live(codoc_dir):
    """Accept verdict applies the plan node and returns its now-live feature id."""
    from codoc.loop import inbox

    res = tools.plan_add(codoc_dir, title="Dark mode", description="theme toggle")
    eid = res["event_id"]
    # User accepts in the IDE → verdict lands in inbox.json.
    inbox.append_verdict(codoc_dir, eid, accept=True)

    out = tools.await_verdicts(codoc_dir, event_ids=[eid], timeout=5, poll_interval=0.01)
    assert out["timed_out"] is False
    assert out["rejected"] == [] and out["pending"] == []
    assert len(out["accepted"]) == 1
    acc = out["accepted"][0]
    assert acc["event_id"] == eid and acc["title"] == "Dark mode"
    assert acc["feature_id"]

    s = open_store(codoc_dir)
    try:
        live = next(f for f in s.list_features() if f.title == "Dark mode")
        assert live.id == acc["feature_id"]
        assert live.realized is False          # accepted but not yet implemented
        assert s.get_event(eid) is None        # event consumed
    finally:
        s.close()
    # inbox was cleared of the consumed verdict
    assert inbox.read_verdicts(codoc_dir) == []
    # the accepted node is flagged "editing" so the IDE shimmers it as in-progress
    from codoc.loop.activity import read_activity
    feats = read_activity(codoc_dir).get("features", {})
    assert feats.get(acc["feature_id"], {}).get("phase") == "editing"


def test_await_verdicts_reject_discards(codoc_dir):
    from codoc.loop import inbox

    res = tools.plan_add(codoc_dir, title="Throwaway", description="nope")
    eid = res["event_id"]
    inbox.append_verdict(codoc_dir, eid, accept=False)

    out = tools.await_verdicts(codoc_dir, event_ids=[eid], timeout=5, poll_interval=0.01)
    assert out["accepted"] == [] and out["rejected"] == [eid]
    s = open_store(codoc_dir)
    try:
        assert all(f.title != "Throwaway" for f in s.list_features())
        assert s.get_event(eid) is None
    finally:
        s.close()


def test_await_verdicts_drains_only_its_own(codoc_dir):
    """A verdict for an unrelated event is left in the inbox for the daemon."""
    from codoc.loop import inbox

    mine = tools.plan_add(codoc_dir, title="Mine")["event_id"]
    inbox.append_verdict(codoc_dir, mine, accept=True)
    inbox.append_verdict(codoc_dir, "e-someone-else", accept=True)

    out = tools.await_verdicts(codoc_dir, event_ids=[mine], timeout=5, poll_interval=0.01)
    assert [a["event_id"] for a in out["accepted"]] == [mine]
    leftover = inbox.read_verdicts(codoc_dir)
    assert [v.event_id for v in leftover] == ["e-someone-else"]


def test_await_verdicts_times_out_when_no_verdict(codoc_dir):
    res = tools.plan_add(codoc_dir, title="Pending forever")
    eid = res["event_id"]
    out = tools.await_verdicts(codoc_dir, event_ids=[eid], timeout=0.05, poll_interval=0.01)
    assert out["timed_out"] is True
    assert out["pending"] == [eid]
    assert out["accepted"] == [] and out["rejected"] == []
