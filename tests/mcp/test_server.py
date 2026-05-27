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
