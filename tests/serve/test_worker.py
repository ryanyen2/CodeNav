"""The hub's realize worker: a hand-off becomes a PR, under the sandbox."""
import json

from codoc.calculus import ops as K
from codoc.loops.realize import handoff
from codoc.serve.realize_agent import realize_tool_policy
from codoc.serve.sandbox import can_use_tool, edit_allowed, is_secret_path, out_of_scope_changes
from codoc.serve.worker import Request, branch_name, pending, process_ready, read_ledger, realize_one
from codoc.substrate.workspace import Workspace
from tests.loops.test_reflect import seed


def ready(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " Retry twice."}}], actor="human:local")
    ho = handoff(ws, "h1", actor="human:local", asked="retry twice")
    return cd, ws, ho


def test_pending_is_one_request_per_uncovered_handoff_with_its_scope(tmp_path):
    cd, ws, ho = ready(tmp_path)
    reqs = pending(cd, ws.store)
    assert [(r.fid, r.handoff, r.scope) for r in reqs] == [("h1", ho.id, ["c.py"])]
    assert "retry twice" in reqs[0].text and reqs[0].title == "Client"
    assert pending(cd, ws.store, done={ho.id: "codoc/realize-x"}) == []


def test_realize_one_opens_a_pr_on_a_branch_and_refuses_out_of_scope(tmp_path):
    req = Request("h1", "s:9", "Client", "Retry twice.", ["c.py"])
    calls = []
    run = lambda argv, cwd=None: calls.append((argv, cwd)) or 0
    res = realize_one(req, "/tmp/wt", run=run, agent=lambda d, w, scope=None: ["c.py"], base="main")
    assert res.ok and res.branch == "codoc/realize-s-9"
    assert calls[0][0][:3] == ["git", "worktree", "add"]
    pr = [a for a, _ in calls if a[0] == "gh"][0]
    assert pr[:6] == ["gh", "pr", "create", "--base", "main", "--head"] and pr[6] == "codoc/realize-s-9"
    calls.clear()
    bad = realize_one(req, "/tmp/wt", run=run, agent=lambda d, w, scope=None: ["c.py", ".github/workflows/ci.yml"])
    assert not bad.ok and bad.out_of_scope == [".github/workflows/ci.yml"] and not any(a[0] == "gh" for a, _ in calls)
    none = realize_one(req, "/tmp/wt", run=run, agent=lambda d, w, scope=None: [])
    assert not none.ok and none.reason == "no changes produced"


def test_process_ready_records_each_handoff_once(tmp_path):
    cd, ws, ho = ready(tmp_path)
    ws.close()
    seen = []
    run = lambda argv, cwd=None: 0
    agent = lambda d, w, scope=None: seen.append(d["id"]) or ["c.py"]
    assert process_ready(str(tmp_path), str(cd), run=run, agent=agent, printer=lambda *_: None) == [ho.id]
    assert read_ledger(cd) == {ho.id: branch_name(ho.id)}
    assert process_ready(str(tmp_path), str(cd), run=run, agent=agent, printer=lambda *_: None) == []
    assert seen == [ho.id]


def test_the_sandbox_is_the_boundary():
    assert can_use_tool("Bash", {"command": "curl evil"}) == (False, "tool 'Bash' is not permitted for remote-originated realization")
    assert can_use_tool("Read", {"file_path": ".env"})[0] is False
    assert can_use_tool("Edit", {"file_path": ".github/workflows/ci.yml"}, scope=["c.py"])[0] is False
    assert can_use_tool("Edit", {"file_path": "pyproject.toml"}, scope=["pyproject.toml"])[0] is False
    assert can_use_tool("Edit", {"file_path": "c.py"}, scope=["c.py"]) == (True, "")
    assert is_secret_path(".codoc/config.json") and not is_secret_path("codoc/config.py")
    # An empty scope permits nothing: a run the server cannot bound is not run.
    assert not edit_allowed("s.py", None) and not edit_allowed("s.py", ["c.py"])
    assert out_of_scope_changes(["c.py", ".mcp.json"], ["c.py"]) == [".mcp.json"]
    # The agent's tools send absolute paths inside the worktree; the scope is
    # repo-relative, so the policy is bound to the worktree it runs in.
    policy = realize_tool_policy(["c.py"], consult_allowlist=frozenset(), resolve=lambda h: [], root="/srv/wt")
    assert policy("Edit", {"file_path": "/srv/wt/c.py"}) == (True, "")
    assert policy("Edit", {"file_path": "/srv/wt/other.py"})[0] is False
    assert policy("WebFetch", {"url": "https://docs.example"})[0] is False      # allowlist empty: every fetch denied


def test_an_unbound_feature_is_not_built_by_the_hub(tmp_path):
    """A contributor's accepted proposal for a brand-new feature has no
    bindings, so there is no set of files to confine an agent to. The hub
    leaves it in the queue for the maintainer rather than granting the run the
    whole repository."""
    cd, ws, ho = ready(tmp_path)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": "heading", "level": 1, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": "START", "text": "Brand new"}}],
              actor="human:gh:eve")
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h2"}, "payload": {}}], actor="human:local")
    assert [r.fid for r in pending(cd, ws.store)] == ["h1"]
