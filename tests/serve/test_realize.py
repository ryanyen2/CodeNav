"""U7 — server-triggered realization → worktree → PR, with the safety invariants.

The agent + subprocess runner are injected, so the five flows assert the SEQUENCE
and the safety gates (PR-only/never-main, out-of-scope abort, agent-holds-no-token,
trigger only on hand-off) without git, gh, or the SDK."""
from __future__ import annotations

from codoc.serve.realize_pr import branch_name, realize_directive
from codoc.serve.realize_trigger import ready_directives


class FakeRun:
    def __init__(self):
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, argv, cwd=None):
        self.calls.append((argv, cwd))
        return 0

    @property
    def argvs(self):
        return [a for a, _ in self.calls]


def _agent(changed):
    def agent(directive, worktree_path, scope=None):
        agent.seen_scope = scope
        agent.seen_worktree = worktree_path
        return changed
    agent.seen_scope = "UNSET"
    return agent


# Flow 1 — a clean realization opens a PR on a feature branch, never pushing to main.
def test_clean_realize_opens_pr_on_branch():
    run = FakeRun()
    agent = _agent(["codoc/auth/session.py"])
    res = realize_directive(
        {"id": "d-abc", "feature_id": "f-1", "title": "Split auth"},
        "/tmp/wt", run=run, agent=agent, scope=["codoc/auth/session.py"])
    assert res.ok is True
    assert res.branch == "codoc/realize-d-abc"
    assert ["git", "worktree", "add", "-b", "codoc/realize-d-abc", "/tmp/wt"] in run.argvs
    pr = next(a for a in run.argvs if a[:3] == ["gh", "pr", "create"])
    assert pr[pr.index("--base") + 1] == "main"
    assert pr[pr.index("--head") + 1] == "codoc/realize-d-abc"
    assert not any(a[:2] == ["git", "push"] for a in run.argvs)  # never push to base
    # the agent ran sandboxed with the scope and was handed no token
    assert agent.seen_scope == ["codoc/auth/session.py"]


# Flow 2 — a write outside scope aborts before any commit / PR (U11 post-run gate).
def test_out_of_scope_write_aborts_pr():
    run = FakeRun()
    agent = _agent(["codoc/auth/session.py", ".github/workflows/ci.yml"])
    res = realize_directive(
        {"id": "d-1"}, "/tmp/wt", run=run, agent=agent, scope=["codoc/auth/session.py"])
    assert res.ok is False
    assert ".github/workflows/ci.yml" in res.out_of_scope
    assert not any(a[:3] == ["gh", "pr", "create"] for a in run.argvs)
    assert not any(a[:2] == ["git", "commit"] for a in run.argvs)


# Flow 3 — no changes produced → no PR.
def test_no_changes_produces_no_pr():
    run = FakeRun()
    res = realize_directive({"id": "d-2"}, "/tmp/wt", run=run, agent=_agent([]), scope=None)
    assert res.ok is False
    assert "no changes" in res.reason
    assert not any(a[:3] == ["gh", "pr", "create"] for a in run.argvs)


# Flow 4 — branch name is derived/sanitized from the directive id.
def test_branch_name_sanitized():
    assert branch_name("d-abc123") == "codoc/realize-d-abc123"
    assert branch_name("d/weird id!") == "codoc/realize-d-weird-id-"


# Flow 5 — the trigger fires only on handed-off directives, only when work is pending.
def test_trigger_only_on_handed_off():
    manifest = [{"id": "d1", "handed_off": True}, {"id": "d2", "handed_off": False}]
    ready = ready_directives({"state": "awaiting_impl"}, manifest)
    assert [d["id"] for d in ready] == ["d1"]
    assert ready_directives({"state": "in_sync"}, manifest) == []
    assert ready_directives({"state": "awaiting_impl"}, []) == []
