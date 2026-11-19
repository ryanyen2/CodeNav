"""The hub's realize worker: a hand-off becomes a PR, under the sandbox."""
import os
import time

from codoc.calculus import ops as K
from codoc.serve.realize_agent import realize_tool_policy
from codoc.serve.sandbox import (
    can_use_tool,
    edit_allowed,
    is_secret_path,
    out_of_scope_changes,
)
from codoc.serve.worker import (
    Request,
    branch_name,
    pending,
    process_ready,
    read_ledger,
    realize_one,
)
from codoc.workspace import Workspace
from codoc.workspace import inbox as I
from tests.helpers import handoff, seed_loop, sub


def ready(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed_loop(ws)
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


def test_no_binding_an_open_plan_made_widens_the_agents_scope(tmp_path):
    """A hub contributor may not attach code (every ``bind.*`` op is refused
    them), and an agent's attach under its open plan is the plan's until a
    verdict, so neither puts a file in the scope the agent may edit."""
    cd, ws, ho = ready(tmp_path)
    I.submit(cd, "hub-eve", [sub("a1", K.BIND_ATTACH, {"fid": "h1"}, {"symbol": "scripts/release.sh::x"})])
    assert [o.conflict for o in ws.drain_inbox() if o.kind == K.BIND_ATTACH] == ["authority"]
    pid = ws.open_plan(actor="agent:x")
    assert ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"symbol": "d.py::f"}}],
                     actor="agent:x", plan=pid)[0].conflict is None
    assert [r.scope for r in pending(cd, ws.store)] == [["c.py"]]
    assert ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:local")[0].conflict is None
    assert [r.scope for r in pending(cd, ws.store)] == [["c.py", "d.py"]]
    ws.close()


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
    bad = realize_one(req, "/tmp/wt", run=run, agent=lambda d, w, scope=None: ["c.py", ".github/workflows/ci.yml"], base="main")
    assert not bad.ok and bad.out_of_scope == [".github/workflows/ci.yml"] and not any(a[0] == "gh" for a, _ in calls)
    none = realize_one(req, "/tmp/wt", run=run, agent=lambda d, w, scope=None: [], base="main")
    assert not none.ok and none.reason == "no changes produced"


def test_process_ready_records_each_handoff_once(tmp_path):
    cd, ws, ho = ready(tmp_path)
    ws.close()
    seen = []
    run = lambda argv, cwd=None: 0
    agent = lambda d, w, scope=None: seen.append(d["id"]) or ["c.py"]
    assert process_ready(str(tmp_path), str(cd), base="main", run=run, agent=agent, printer=lambda *_: None) == [ho.id]
    assert read_ledger(cd) == {ho.id: branch_name(ho.id)}
    assert process_ready(str(tmp_path), str(cd), base="main", run=run, agent=agent, printer=lambda *_: None) == []
    assert seen == [ho.id]


def test_the_sandbox_is_the_boundary(tmp_path):
    wt = str(tmp_path / "wt")
    assert can_use_tool("Bash", {"command": "curl evil"}, root=wt) == (False, "tool 'Bash' is not permitted for remote-originated realization")
    assert can_use_tool("Read", {"file_path": f"{wt}/.env"}, root=wt)[0] is False
    assert can_use_tool("Edit", {"file_path": f"{wt}/.github/workflows/ci.yml"}, scope=["c.py"], root=wt)[0] is False
    assert can_use_tool("Edit", {"file_path": f"{wt}/pyproject.toml"}, scope=["pyproject.toml"], root=wt)[0] is False
    assert can_use_tool("Edit", {"file_path": f"{wt}/c.py"}, scope=["c.py"], root=wt) == (True, "")
    assert is_secret_path(".codoc/config.json") and not is_secret_path("codoc/config.py")
    # A source file named after what it implements is not a secret.
    assert not is_secret_path("tokenizer.py") and edit_allowed("auth/password_reset.py", ["auth/password_reset.py"])
    # An empty scope permits nothing: a run the server cannot bound is not run.
    assert not edit_allowed("s.py", None) and not edit_allowed("s.py", ["c.py"])
    assert out_of_scope_changes(["c.py", ".mcp.json"], ["c.py"]) == [".mcp.json"]
    # The agent's tools send absolute paths inside the worktree; the scope is
    # repo-relative, so the policy is bound to the worktree it runs in.
    policy = realize_tool_policy(["c.py"], consult_allowlist=frozenset(), resolve=lambda h: [], root="/srv/wt")
    assert policy("Edit", {"file_path": "/srv/wt/c.py"}) == (True, "")
    assert policy("Edit", {"file_path": "/srv/wt/other.py"})[0] is False
    assert policy("WebFetch", {"url": "https://docs.example"})[0] is False      # allowlist empty: every fetch denied


def test_a_branch_the_agent_already_built_is_published_not_rebuilt(tmp_path):
    """The `built:` ledger entry is written after the push and the PR have
    both been tried, so a hub killed in between leaves the commit on the branch
    and nothing in the ledger. A branch by the hand-off's name is the record:
    the next pass resumes at the push and does not delete the agent's work or
    run the model again."""
    cd, ws, ho = ready(tmp_path)
    ws.close()
    branch = branch_name(ho.id)
    branches = {branch}                                   # left behind by the crashed pass
    calls: list[list[str]] = []

    def run(argv, cwd=None):
        calls.append(list(argv))
        if argv[:3] == ["git", "worktree", "add"]:
            if "-b" in argv:
                b = argv[argv.index("-b") + 1]
                if b in branches:
                    return 128                            # fatal: a branch named '…' already exists
                branches.add(b)
                return 0
            return 0 if argv[-1] in branches else 128
        if argv[:2] == ["git", "branch"] and "-D" in argv:
            branches.discard(argv[-1])
            return 0
        return 0

    runs: list[str] = []
    process_ready(str(tmp_path), str(cd), base="main", run=run, agent=lambda d, w, scope=None: runs.append(d["id"]) or ["c.py"],
                  printer=lambda *_: None, retry=[])
    assert runs == [], f"the agent ran again for work already committed: {runs}"
    names = [" ".join(c[:3]) for c in calls]
    assert "git push -u" in names, names
    # The local branch goes only AFTER the push and the PR: the work is on the
    # remote by then, which is what `_cleanup` is for.
    assert names.index("git push -u") < names.index("git branch -D")


def test_a_merged_hub_pull_request_settles_its_handoff(tmp_path, monkeypatch):
    """The worker's commit names the hand-off in a `Codoc-Handoff` trailer. The
    daemon that sees HEAD move over that commit reflects the merged code citing
    the hand-off, and the hand-off settles on those binding ops. Real git; only
    the push and `gh` are faked."""
    import subprocess
    from pathlib import Path
    from codoc.daemon.daemon import Daemon
    from codoc.index.changes import ChangeSet, ChunkRef
    from codoc.calculus.derive import queued
    for k, v in {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
                 "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@example.com"}.items():
        monkeypatch.setenv(k, v)
    root = tmp_path / "repo"
    (root / ".codoc").mkdir(parents=True)

    def git(*argv):
        subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    (root / "c.py").write_text("def send(): ...\n")
    git("add", "c.py")
    git("commit", "-qm", "init")
    d = Daemon(root, changeset=lambda *a: ChangeSet(modified=[ChunkRef("c.py::send", "f2", "t1")]),
               propose=lambda *a, **k: [], printer=lambda *_: None)
    seed_loop(d.ws)
    ho = handoff(d.ws, "h1", actor="human:local")

    def run(argv, cwd=None):
        if argv[:2] == ["git", "push"] or argv[0] == "gh":
            return 0
        return subprocess.run(argv, cwd=cwd, capture_output=True).returncode

    def agent(_directive, worktree, scope=None):
        Path(worktree, "c.py").write_text("def send(): retry()\n")
        return ["c.py"]
    [req] = pending(d.codoc_dir, d.ws.store, {})
    assert realize_one(req, str(tmp_path / "wt"), run=run, agent=agent, base="main", root=str(root)).ok
    git("merge", "-q", "--ff-only", branch_name(ho.id))          # the pull request merges and is pulled
    d.process_batch([str(root / "c.py")])
    st = d.ws.store
    assert queued(st) == []
    assert [o.caused_by for o in st.ops if o.kind == K.FEATURE_REALIZED] == [ho.id]
    assert any(o.kind == K.BIND_REFRESH and o.caused_by == ho.id for o in st.ops)


def test_every_path_taking_tool_stays_inside_the_worktree():
    """Write is confined to the files the directive names, so an unconfined Read
    is an exfiltration path with a publisher attached: the agent reads the `gh`
    token that opens the pull request, writes it into an in-scope file, and the
    diff carries it to whoever asked for the change."""
    wt = "/srv/repo/.codoc/worktrees/codoc-realize-s-9"
    policy = realize_tool_policy(["c.py"], consult_allowlist=frozenset(), resolve=lambda h: [], root=wt)
    for outside in ("~/.config/gh/hosts.yml", "~/.netrc", "/proc/1/environ", "/etc/passwd",
                    "/srv/other-repo/.codoc/inbox/ide.jsonl"):
        assert policy("Read", {"file_path": os.path.expanduser(outside)})[0] is False, outside
    assert policy("Grep", {"pattern": "oauth_token", "path": os.path.expanduser("~")})[0] is False
    assert policy("Glob", {"pattern": "**/hosts.yml", "path": "/"})[0] is False
    # A Glob takes its target in `pattern` too, and an absolute one lists files
    # anywhere. `Read` still refuses them, so what leaks is names — which is
    # not a thing a boundary that refuses the contents should answer.
    assert policy("Glob", {"pattern": os.path.expanduser("~/.ssh/*")})[0] is False
    assert policy("Glob", {"pattern": "/Users/*/.config/gh/*"})[0] is False
    assert policy("Glob", {"pattern": "**/*.py"}) == (True, "")            # relative: the worktree
    # No path named at all: the tool runs from the worktree, which is the boundary.
    assert policy("Grep", {"pattern": "send"}) == (True, "")
    # The worktree lives under the maintainer's own `.codoc/`, so the denylists
    # must read the path the scope names, not the absolute one.
    assert policy("Read", {"file_path": f"{wt}/c.py"}) == (True, "")
    assert policy("Read", {"file_path": f"{wt}/.codoc/config.json"})[0] is False


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


def test_a_sandbox_that_could_not_run_is_not_a_refusal(tmp_path):
    """`make_sandboxed_agent` fails safe when the SDK is missing or the hook is
    unsupported. Reading that as "produced nothing" buried the hand-off in the
    ledger for ever, so a hub without the SDK silently refused every request it
    would ever see, with the wrong reason."""
    cd, ws, ho = ready(tmp_path)
    ws.close()
    out = []
    retry = []
    process_ready(str(tmp_path), str(cd), base="main", run=lambda a, cwd=None: 0, agent=lambda *a, **k: None,
                  printer=out.append, retry=retry)
    assert ho.id not in read_ledger(cd), read_ledger(cd)
    assert retry == [ho.id] and any("will retry" in line for line in out), out


def test_the_worker_comes_back_to_an_unanswered_request_before_the_tree_moves(tmp_path, monkeypatch):
    """docs/serve-deployment.md: "a git or gh failure is not a refusal and is
    retried". The loop skips a pass when the published head has not moved, so
    without this the retry waited for somebody else to edit the tree."""
    from codoc.serve import worker as W
    passes = []

    def fake(root, cd, *, base, printer=print, retry=None, **kw):
        passes.append(1)
        if retry is not None and len(passes) < 3:
            retry.append("s:9")            # still unanswered
        return []
    monkeypatch.setattr(W, "_published_head", lambda cd: 7)
    monkeypatch.setattr(W, "process_ready", fake)
    w = W.Worker(str(tmp_path), str(tmp_path / ".codoc"), base="main", interval=0.01, printer=lambda *_: None).start()
    time.sleep(0.2)
    w.stop()
    assert len(passes) == 3, f"{len(passes)} pass(es): the retry stopped early or never stopped"


def test_the_live_sdk_run_gates_every_tool_through_a_hook(monkeypatch):
    """An `allowed_tools` entry naming a whole tool auto-approves that tool
    before `can_use_tool` runs, so the live run gates every tool through a
    PreToolUse hook, which runs whatever the allow rules say."""
    from codoc.serve import realize_agent as R
    captured = {}

    def fake_query(*, prompt, options):
        captured["options"] = options

        async def none():
            return
            yield
        return none()

    import claude_agent_sdk
    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    assert R._run_sandboxed_sdk(prompt="do it", worktree_path="/tmp/wt",
                                policy=lambda *_a: (False, "no")) is True
    opts = captured["options"]
    assert getattr(opts, "hooks", None), "no PreToolUse hook: the policy is never consulted"
    hook = opts.hooks["PreToolUse"][0].hooks[0]
    import asyncio
    out = asyncio.run(hook({"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}}, None, None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "Bash" in (opts.disallowed_tools or [])
    # …and the SDK itself agrees the callback would have been shadowed, which is
    # why the hook and not the callback is the boundary.
    from claude_agent_sdk.types import _get_can_use_tool_shadowed_warning as shadowed
    assert shadowed(opts.permission_mode, list(opts.allowed_tools)) is not None


def test_every_github_credential_is_scrubbed_from_the_agents_environment():
    from codoc.serve.realize_agent import _GH_TOKEN_KEYS
    assert {"CODOC_GITHUB_TOKEN", "CODOC_GITHUB_CLIENT_SECRET", "GITHUB_TOKEN", "GH_TOKEN"} <= set(_GH_TOKEN_KEYS)


def test_a_grep_pattern_is_a_regex_and_a_glob_pattern_is_a_path():
    """`Grep`'s `pattern` is a regex and is not checked as a path, so a search
    for `/api/message` is allowed. `Glob`'s `pattern` is a path, so one that
    climbs out with `..` or names another home directory is refused."""
    wt = "/srv/repo/.codoc/worktrees/codoc-realize-s-9"
    policy = realize_tool_policy(["c.py"], consult_allowlist=frozenset(), resolve=lambda h: [], root=wt)
    assert policy("Grep", {"pattern": "/api/message"}) == (True, "")
    assert policy("Grep", {"pattern": "^~/.*$"}) == (True, "")
    assert policy("Grep", {"pattern": "oauth", "path": os.path.expanduser("~")})[0] is False
    assert policy("Glob", {"pattern": "**/*.py"}) == (True, "")
    assert policy("Glob", {"pattern": "/Users/*/.config/gh/*"})[0] is False
    assert policy("Glob", {"pattern": "../../etc/*"})[0] is False
    assert policy("Glob", {"pattern": "src/../../../etc/*"})[0] is False


def test_a_ledger_that_does_not_parse_is_kept_before_a_new_one_is_written(tmp_path):
    """The ledger says which hand-offs already have pull requests. One that
    does not parse is moved aside, not written over, so what it held can be
    recovered by hand."""
    from codoc.serve.worker import record_pr
    cd = tmp_path / ".codoc"
    (cd / "cache").mkdir(parents=True)
    (cd / "cache" / "hub-prs.json").write_text('{"d-1": "codoc/realize-d-1",}')
    record_pr(cd, "d-2", "codoc/realize-d-2")
    kept = [p for p in (cd / "cache").iterdir() if p.name.startswith("hub-prs.json.corrupt-")]
    assert [p.read_text() for p in kept] == ['{"d-1": "codoc/realize-d-1",}']
    assert read_ledger(cd) == {"d-2": "codoc/realize-d-2"}
