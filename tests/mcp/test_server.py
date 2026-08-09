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
    assert sidecar["version"] == 6


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


# ─── Fix A: agent-native parity — drift + dead-ref surfaced via the MCP tools ──

def test_read_tree_surfaces_drift_state_for_questioned_feature(codoc_dir):
    """read_tree carries each feature's loop-computed drift state (from
    drift.json) so a reconciling agent sees which features are `questioned` /
    `binding-lost`; a `followed` feature has drift=None."""
    from codoc.loop.edits import DRIFT_QUESTIONED, write_drift

    questioned = _seed(codoc_dir, title="Validator", description="Validates input.")
    followed = _seed(codoc_dir, title="Router", description="Routes requests.")

    # A prior code-side pass questioned the validator (its bound code drifted).
    write_drift(codoc_dir, {questioned.id: DRIFT_QUESTIONED})

    tree = tools.read_tree(codoc_dir)
    by_id = {f["id"]: f for f in tree["features"]}
    assert by_id[questioned.id]["drift"] == DRIFT_QUESTIONED
    assert by_id[followed.id]["drift"] is None   # absence = followed = no badge


def test_read_tree_drift_field_is_none_without_drift_file(codoc_dir):
    """No drift.json (never a loop pass) → every feature's drift is None, tolerant."""
    f = _seed(codoc_dir, title="Auth", description="Logs users in.")
    tree = tools.read_tree(codoc_dir)
    assert tree["features"][0]["id"] == f.id
    assert tree["features"][0]["drift"] is None


def test_read_status_reports_dead_ref_from_registry(codoc_dir):
    """read_status summarizes unresolved inline refs from tree.index.json: a feature
    whose description cites code with no backing binding is a dead ref."""
    from codoc.codoc_file.render import write_registry

    # A feature that cites code which is NOT bound anywhere → the ref is unresolved.
    f = _seed(codoc_dir, title="Cache",
              description="Caches via [get](codoc:cache.py#get).")

    s = open_store(codoc_dir)
    try:
        write_registry(s, codoc_dir)   # derives refs + resolved flags
    finally:
        s.close()

    st = tools.read_status(codoc_dir)
    assert st["dead_refs"] == 1
    assert st["dead_ref_list"] == [
        {"feature_id": f.id, "file": "cache.py", "symbol": "get"}
    ]


def test_read_status_no_dead_refs_when_ref_is_bound(codoc_dir):
    """A cited symbol that IS bound resolves → no dead ref reported."""
    from codoc.codoc_file.render import write_registry
    from codoc.model.binding import Binding

    f = _seed(codoc_dir, title="Cache",
              description="Caches via [get](codoc:cache.py#get).")
    s = open_store(codoc_dir)
    try:
        s.upsert_binding(Binding(feature_id=f.id, file="cache.py",
                                 symbol_path="cache.py::get", fingerprint="h"))
        write_registry(s, codoc_dir)
    finally:
        s.close()

    st = tools.read_status(codoc_dir)
    assert st["dead_refs"] == 0
    assert st["dead_ref_list"] == []


def test_read_status_tolerates_missing_registry(codoc_dir):
    """No tree.index.json → dead-ref summary degrades to empty (no crash)."""
    _seed(codoc_dir, title="Auth")
    st = tools.read_status(codoc_dir)
    assert st["dead_refs"] == 0
    assert st["dead_ref_list"] == []


# ─── doc always wins: an agent may not overwrite prose the author is editing ──

def _hold_feature(codoc_dir, fid: str, *, directive_id: str = "d-1", handed_off: bool = False):
    """Put `fid` in the hold set the way a real held draft does. The timestamp must
    be recent: an abandoned draft deliberately stops holding (see hold_set)."""
    import time

    from codoc.loop import edits

    edits.write_manifest(codoc_dir, [
        edits.Directive(id=directive_id, feature_id=fid, kind="amend",
                        handed_off=handed_off, ts=int(time.time() * 1000)),
    ])


def test_agent_amend_on_a_held_feature_becomes_a_proposal_not_an_overwrite(codoc_dir):
    """The author is mid-edit on this feature (it holds a draft). An agent amend
    small enough to auto-apply would have rewritten their prose out from under
    them — the MCP path never checked holds, though Loop A always has. It must
    surface for review instead, and the stored description must not move."""
    f = _seed(codoc_dir, title="Auth", description="The author is still writing this sentence here.")
    _hold_feature(codoc_dir, f.id)

    res = tools.reflect(codoc_dir, ops=[
        {"kind": "amend", "feature_id": f.id, "description": "The author is still writing this sentence here!"},
    ])

    assert res["ok"] and res["results"][0]["applied"] is False
    with open_store(codoc_dir) as s:
        assert s.get_feature(f.id).description == "The author is still writing this sentence here."
        assert len(s.pending_events()) == 1   # kept for review, not discarded


def test_an_agent_completing_its_own_directive_still_applies(codoc_dir):
    """When an agent finishes realizing a directive it reflects the result while
    that very directive still holds the feature. It is completing the hold, not
    fighting it — blocking this would break the loop's closing step."""
    f = _seed(codoc_dir, title="Auth", description="Validates the session token on every request.")
    _hold_feature(codoc_dir, f.id, directive_id="d-mine", handed_off=True)
    (__import__("pathlib").Path(codoc_dir) / "realize.md").write_text("### 1. ⟨d-mine⟩ …")

    res = tools.reflect(codoc_dir, ops=[
        {"kind": "amend", "feature_id": f.id, "description": "Validates the session token on every request!"},
    ], caused_by="d-mine")

    assert res["results"][0]["applied"] is True
    with open_store(codoc_dir) as s:
        assert s.get_feature(f.id).description == "Validates the session token on every request!"


def test_an_unheld_feature_is_unaffected(codoc_dir):
    """No hold, no change in behaviour — small amends still apply straight through."""
    f = _seed(codoc_dir, title="Auth", description="Validates the session token on every request.")

    res = tools.reflect(codoc_dir, ops=[
        {"kind": "amend", "feature_id": f.id, "description": "Validates the session token on every request!"},
    ])

    assert res["results"][0]["applied"] is True


def test_binding_maintenance_is_never_suppressed_by_a_hold(codoc_dir):
    """Bindings are attribution, not intent: code attribution must stay correct
    while the author edits prose (classify row 13 exempts attach/detach/refresh)."""
    f = _seed(codoc_dir, title="Auth", description="mid-edit")
    _hold_feature(codoc_dir, f.id)

    res = tools.reflect(codoc_dir, ops=[{"kind": "attach", "feature_id": f.id, "binds": ["a.py::x"]}])

    assert res["results"][0]["applied"] is True


# ── ordering is not a human-only capability ───────────────────────────────────
#
# The rank machinery is symmetric — `apply_op` resolves after_id/before_id at the write
# boundary for any source — but the agent surfaces dropped the anchors before they got
# there: `reflect` rebuilt each op without them and `propose_move` had no order params.
# So every agent move appended, and an agent that split a feature could not put the new
# node beside the one it came from. These pin the plumbing end to end.

def _accept_all(codoc_dir):
    """Apply every pending proposal, the way an IDE accept does."""
    from codoc.loop.apply import apply_op

    with open_store(codoc_dir) as s:
        for ev in s.pending_events():
            apply_op(ev.op, s, source=ev.source, applied=True)
            s.mark_applied(ev.id)


def _order(codoc_dir, parent=None):
    with open_store(codoc_dir) as s:
        return [f.title for f in s.children(parent)]


def _seed_ranked(codoc_dir, *titles) -> list[Feature]:
    out = []
    with open_store(codoc_dir) as s:
        for t in titles:
            f = Feature(title=t, rank=s.rank_for_append(None))
            s.upsert_feature(f)
            out.append(f)
    return out


def test_reflect_honours_the_sibling_anchors_on_a_move(codoc_dir):
    a, b, c = _seed_ranked(codoc_dir, "Alpha", "Beta", "Gamma")
    assert _order(codoc_dir) == ["Alpha", "Beta", "Gamma"]

    res = tools.reflect(codoc_dir, ops=[{
        "kind": "move_node", "feature_id": c.id, "parent_id": None,
        "after_id": a.id, "before_id": b.id, "rationale": "belongs beside Alpha",
    }])
    assert res["ok"]
    _accept_all(codoc_dir)

    assert _order(codoc_dir) == ["Alpha", "Gamma", "Beta"]


def test_reflect_without_anchors_still_appends(codoc_dir):
    """The behaviour every caller had before ordering existed: no opinion → last."""
    a, b = _seed_ranked(codoc_dir, "Alpha", "Beta")
    with open_store(codoc_dir) as s:
        child = Feature(title="Child", parent_id=a.id, rank=s.rank_for_append(a.id))
        s.upsert_feature(child)

    tools.reflect(codoc_dir, ops=[{"kind": "move_node", "feature_id": child.id,
                                  "parent_id": None}])
    _accept_all(codoc_dir)

    assert _order(codoc_dir) == ["Alpha", "Beta", "Child"]


def test_propose_move_can_reorder_within_one_parent(codoc_dir):
    """A reorder IS a move whose parent is unchanged and whose anchors differ — the
    gesture a human drag makes, now sayable by an agent."""
    a, b, c = _seed_ranked(codoc_dir, "Alpha", "Beta", "Gamma")

    res = tools.propose_move(codoc_dir, feature_id=a.id, parent_id=None,
                             after_id=b.id, before_id=c.id, rationale="reads better here")
    assert res["ok"]
    _accept_all(codoc_dir)

    assert _order(codoc_dir) == ["Beta", "Alpha", "Gamma"]


def test_propose_add_lands_between_the_siblings_it_names(codoc_dir):
    a, b = _seed_ranked(codoc_dir, "Alpha", "Gamma")

    tools.propose_add(codoc_dir, title="Beta", description="in between",
                      after_id=a.id, before_id=b.id)
    _accept_all(codoc_dir)

    assert _order(codoc_dir) == ["Alpha", "Beta", "Gamma"]
