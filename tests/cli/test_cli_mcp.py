"""The command line and the agent's tools, end to end without an LLM or a daemon."""
import json

from typer.testing import CliRunner

from codoc.cli.main import app
from codoc.daemon import client, reads
from codoc.daemon.publish import publish
from codoc.index.graph import Graph
from codoc.loops.bootstrap import bootstrap
from codoc.mcp import tools
from codoc.workspace import Workspace
from tests.helpers import ROWS, fake_file, fake_org, index_holds

runner = CliRunner()


def seeded(tmp_path):
    root = tmp_path / "repo"
    cd = root / ".codoc"
    cd.mkdir(parents=True)
    ws = Workspace(cd)
    bootstrap(ws, ROWS, Graph(), propose_file=fake_file, propose_org=fake_org, propose_the_brief=lambda *a, **k: {}, concurrency=1)
    publish(cd, ws.store, root_dir=str(root))
    ws.close()
    return root, cd


def test_version():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0 and r.output.startswith("codoc "), r.output


def test_status_handoff_history_and_verdicts_via_cli(tmp_path):
    root, cd = seeded(tmp_path)
    r = runner.invoke(app, ["status", "--root", str(root)])
    assert r.exit_code == 0 and "synced" in r.output and "4 features" in r.output
    r = runner.invoke(app, ["handoff", "HTTP client", "--root", str(root), "--asked", "retry thrice"])
    assert r.exit_code == 0 and "handed off" in r.output
    r = runner.invoke(app, ["status", "--root", str(root)])
    assert "queued" in r.output and (cd / "realize.md").read_text().count("Author asked: retry thrice") == 1
    r = runner.invoke(app, ["history", "HTTP client", "--root", str(root)])
    assert r.exit_code == 0 and "feature.handoff" in r.output and "who wrote it:" in r.output
    # An agent proposes an amend; the person rejects it from the CLI.
    st = client.read(cd).store
    client_id = next(h.id for h in st.headings() if "HTTP client" in tools.title(st, h.id))
    body = st.body(client_id)
    res = tools.propose(str(cd), ops=[{"kind": "amend", "feature_id": client_id,
                                       "blocks": [{"id": body[1].id, "text": "Sends requests over HTTP/2."}]}],
                        note="http2", root_dir=str(root))
    assert res["ok"]
    st = client.read(cd).store
    assert st.plans[res["plan_id"]].status == "open"
    assert "HTTP/2" in tools.description(st, client_id)          # live shows the proposal
    assert "HTTP/2" not in tools.description(st, client_id, "rejected")
    r = runner.invoke(app, ["status", "--root", str(root)])          # what `codoc reject` needs
    assert f"plan    {res['plan_id']}  agent:claude-code  http2" in r.output, r.output
    r = runner.invoke(app, ["reject", res["plan_id"], "--root", str(root)])
    assert r.exit_code == 0 and "rejected" in r.output
    assert "HTTP/2" not in tools.description(client.read(cd).store, client_id)


def test_reflect_attaches_and_repairs_and_closes_the_queue(tmp_path, monkeypatch):
    root, cd = seeded(tmp_path)
    index_holds(monkeypatch, "net/client.py::Client.retry")
    st = client.read(cd).store
    client_id = next(h.id for h in st.headings() if "HTTP client" in tools.title(st, h.id))
    runner.invoke(app, ["handoff", client_id, "--root", str(root)])
    ho = client.read(cd).store.watermarks(client_id).handoffs[-1]
    ctx = tools.read_context(str(cd), files=["net/client.py"])
    me = next(e for e in ctx["subtree"] if e["id"] == client_id)
    assert me["blocks"] and "written_by" in me and "phase" not in me
    from codoc.workspace.epoch import write_activity
    write_activity(cd, {"version": 1, "epoch": {"id": "ep-1", "open": True}, "touched": []})   # the agent's turn
    res = tools.reflect(str(cd), ops=[
        {"kind": "attach", "feature_id": client_id, "bindings": ["net/client.py::Client.retry"]},
        {"kind": "amend", "feature_id": client_id, "blocks": [{"id": me["blocks"][0]["id"], "text": "Sends requests, three times if need be."}]},
    ], caused_by=ho, root_dir=str(root))
    assert res["ok"] and all(a["conflict"] is None for a in res["applied"])
    assert res["amended"][0]["refused"] == [] and res["amended"][0]["status"].startswith("plan:")  # queued: a plan
    st = client.read(cd).store
    assert "net/client.py::Client.retry" in st.bindings
    assert "three times" in tools.description(st, client_id)
    assert any(o.caused_by == ho and o.kind == "bind.attach" for o in st.ops)
    status = reads.queue_status(str(cd))
    assert status["doc_language"]["code"] == "en"


def test_tree_read(tmp_path):
    root, cd = seeded(tmp_path)
    t = tools.read_tree(str(cd))
    assert len(t["features"]) == 4 and all("blocks" in f for f in t["features"])


def test_rebuild_and_snapshot_cli(tmp_path):
    root, cd = seeded(tmp_path)
    r = runner.invoke(app, ["snapshot", "--root", str(root)])
    assert r.exit_code == 0 and "snapshot-" in r.output
    r = runner.invoke(app, ["rebuild", "--root", str(root)])
    assert r.exit_code == 0 and "4 features" in r.output
    assert json.loads((cd / "status.json").read_text())["features"] == 4


def test_accepting_a_plan_from_the_shell_hands_its_feature_off(tmp_path):
    """`codoc accept` sends `feature.handoff` for every feature the plan
    touches in the same batch as the verdict, so the accepted feature is
    queued. The applier never adds a hand-off to a verdict; the sender does."""
    from codoc.calculus.derive import queued
    root, cd = seeded(tmp_path)
    st = client.read(cd).store
    fid = next(h.id for h in st.headings() if "HTTP client" in tools.title(st, h.id))
    body = st.body(fid)
    res = tools.propose(str(cd), ops=[{"kind": "amend", "feature_id": fid,
                                       "blocks": [{"id": body[1].id, "text": "Sends requests over HTTP/2."}]}],
                        note="http2", root_dir=str(root))
    assert res["ok"] and queued(client.read(cd).store) == []
    r = runner.invoke(app, ["accept", res["plan_id"], "--root", str(root)])
    assert r.exit_code == 0 and "accepted" in r.output, r.output
    st = client.read(cd).store
    assert st.plans[res["plan_id"]].status == "accepted"
    assert queued(st) == [fid], "accepting the plan did not queue the build"
    assert "HTTP/2" in tools.description(st, fid, "rejected")
    # `asked` is the person's own words; accepting a plan says nothing in them.
    assert not any(line.startswith("Author asked:") for line in (cd / "realize.md").read_text().splitlines())


def test_no_build_takes_the_words_without_the_work(tmp_path):
    from codoc.calculus.derive import queued
    root, cd = seeded(tmp_path)
    st = client.read(cd).store
    fid = next(h.id for h in st.headings() if "HTTP client" in tools.title(st, h.id))
    body = st.body(fid)
    res = tools.propose(str(cd), ops=[{"kind": "amend", "feature_id": fid,
                                       "blocks": [{"id": body[1].id, "text": "Sends requests over HTTP/2."}]}],
                        note="http2", root_dir=str(root))
    r = runner.invoke(app, ["accept", res["plan_id"], "--root", str(root), "--no-build"])
    assert r.exit_code == 0
    st = client.read(cd).store
    assert st.plans[res["plan_id"]].status == "accepted" and queued(st) == []


def test_accepting_a_plan_that_adds_features_queues_each_of_them(tmp_path):
    """An `add` plan names no target — the headings it proposes do not exist
    when it opens — so handing off its target alone queued nothing at all."""
    from codoc.calculus.derive import queued
    root, cd = seeded(tmp_path)
    res = tools.propose(str(cd), ops=[{"kind": "add_node", "title": "Retry policy", "description": "How often."},
                                      {"kind": "add_node", "title": "Back-off", "description": "How long."}],
                        note="two new features", root_dir=str(root))
    assert res["ok"], res
    st = client.read(cd).store
    assert st.plans[res["plan_id"]].target is None
    r = runner.invoke(app, ["accept", res["plan_id"], "--root", str(root)])
    assert r.exit_code == 0, r.output
    st = client.read(cd).store
    added = [h.id for h in st.headings() if tools.title(st, h.id) in ("Retry policy", "Back-off")]
    assert len(added) == 2 and sorted(queued(st)) == sorted(added), (added, queued(st))


def test_sync_applies_under_the_applier_lock(tmp_path, monkeypatch):
    """`codoc sync` is a daemonless applier like the MCP server: its whole
    pass, model call included, runs inside the lock `client.applying` takes."""
    from codoc.daemon.daemon import Daemon
    from codoc.workspace.layout import APPLIER_LOCK, locked
    root, cd = seeded(tmp_path)
    held = []

    def probe(*_a, **_k):
        try:
            with locked(cd / APPLIER_LOCK, timeout=0):
                held.append(False)
        except TimeoutError:
            held.append(True)
        return "probed"
    monkeypatch.setattr(Daemon, "process_batch", lambda self, paths, **k: probe())
    monkeypatch.setattr(Daemon, "reflect", lambda self, files, **k: probe())
    r = runner.invoke(app, ["sync", "--root", str(root)])
    assert r.exit_code == 0 and held == [True, True], (r.output, held)


def test_a_daemon_that_starts_before_the_lock_gets_a_message_not_a_traceback(tmp_path, monkeypatch):
    """The commands that apply themselves learn about a daemon under the lock."""
    root, cd = seeded(tmp_path)
    monkeypatch.setattr(client, "daemon_running", lambda _cd: True)
    for cmd, says in (("rebuild", "A daemon is watching"), ("snapshot", "Stop the daemon"),
                      ("translate", "Stop the daemon")):
        r = runner.invoke(app, [cmd, "--root", str(root)] + (["--to", "fr"] if cmd == "translate" else []))
        assert r.exit_code == 1 and says in r.output, (cmd, r.output, r.exception)


def test_init_that_stops_at_the_install_says_the_document_is_built(tmp_path, monkeypatch):
    from codoc.cli import main
    root = tmp_path / "repo"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "settings.local.json").write_text('{"permissions": {"allow": ["x",]}}')
    monkeypatch.setattr(main, "_bootstrap", lambda *a, **k: type("R", (), {"summary": lambda self: "built"})())
    r = runner.invoke(app, ["init", "--root", str(root)])
    assert r.exit_code == 1 and "The document is built; only the install stopped" in r.output, r.output
    assert "settings.local.json does not parse" in r.output


def test_a_foreground_realize_holds_the_lease_its_agent_runs_under(tmp_path, monkeypatch):
    """`codoc realize --engine cli` takes the realizing lease and names its
    `claude -p` the owner, as a pass the daemon starts does; without the name,
    the agent reads the lease as another pass's and stands down. The lease
    carries the child's pid, so it holds while the child lives."""
    import subprocess
    import sys
    from codoc.claude import autorealize
    from codoc.daemon.publish import REALIZE_OWNER_ENV
    root, cd = seeded(tmp_path)
    runner.invoke(app, ["handoff", "HTTP client", "--root", str(root)])
    started = []

    class Child:
        pid = 4242

        def __init__(self, argv, **kw):
            started.append((argv, kw["env"]))

        def wait(self):
            return 0
    monkeypatch.setattr(autorealize, "find_claude", lambda: "/bin/claude")
    monkeypatch.setattr(subprocess, "Popen", Child)
    r = runner.invoke(app, ["realize", "--engine", "cli", "--root", str(root)])
    assert r.exit_code == 0, r.output
    [(argv, env)] = started
    assert argv == [sys.executable, "-m", "codoc.claude.autorealize", str(root), "acceptEdits"]
    status = json.loads((cd / "status.json").read_text())
    assert status["state"] == "realizing" and status["owner"] == env[REALIZE_OWNER_ENV] and status["pid"] == 4242


def test_the_hubs_pull_requests_target_the_repositorys_default_branch(tmp_path, monkeypatch):
    """Origin's HEAD, else the branch checked out, else `main`: a `master` or
    `trunk` repository is not given pull requests against a branch it lacks."""
    import subprocess
    from codoc.cli.main import _default_branch
    for k, v in {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
                 "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}.items():
        monkeypatch.setenv(k, v)
    root = tmp_path / "repo"

    def git(*argv):
        subprocess.run(["git", "-C", str(root), *argv], check=True, capture_output=True)
    assert _default_branch(str(tmp_path)) == "main"                   # not a repository
    root.mkdir()
    git("init", "-q", "-b", "trunk")
    git("commit", "-q", "--allow-empty", "-m", "init")
    assert _default_branch(str(root)) == "trunk"
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master")
    assert _default_branch(str(root)) == "master"


def test_lang_takes_a_tag_and_writes_it_normalized(tmp_path):
    root, cd = seeded(tmp_path)
    r = runner.invoke(app, ["lang", "not a tag", "--root", str(root)])
    assert r.exit_code == 2 and not (cd / "config.json").exists()
    r = runner.invoke(app, ["lang", "zh", "--root", str(root)])
    assert r.exit_code == 0 and json.loads((cd / "config.json").read_text())["doc_language"] == "zh-Hans"


def test_the_cli_finds_the_workspace_from_a_subdirectory(tmp_path, monkeypatch):
    """As the hooks and the MCP server do; `codoc init` there finds the
    workspace above instead of making a second one."""
    root, cd = seeded(tmp_path)
    sub = root / "src" / "pkg"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0 and "4 features" in r.output, r.output
    assert runner.invoke(app, ["status", "--root", str(sub)]).exit_code == 0
    r = runner.invoke(app, ["init"])
    assert r.exit_code == 1 and "already exists" in r.output and not (sub / ".codoc").exists()
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["status"])
    assert r.exit_code == 1 and "No codoc workspace in" in r.output


def test_history_says_when_what_and_for_which_handoff(tmp_path, monkeypatch):
    """A local time, not the clock's padded stamp; the symbol a binding names;
    the hand-off an op answers; and who wrote each run of words, as words."""
    import re
    root, cd = seeded(tmp_path)
    index_holds(monkeypatch, "net/client.py::Client.retry")
    runner.invoke(app, ["handoff", "HTTP client", "--root", str(root), "--asked", "retry thrice"])
    st = client.read(cd).store
    fid = next(h.id for h in st.headings() if tools.title(st, h.id) == "HTTP client")
    ho = st.watermarks(fid).handoffs[-1]
    assert tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": fid, "bindings": ["net/client.py::Client.retry"]}],
                         caused_by=ho, root_dir=str(root))["ok"]
    out = runner.invoke(app, ["history", "HTTP client", "--root", str(root)]).output
    assert re.search(rf"\n  \d{{4}}-\d\d-\d\d \d\d:\d\d:\d\d  agent:claude-code +bind.attach +"
                     rf"net/client.py::Client.retry  for hand-off d:{ho}\n", out), out
    assert "feature.handoff 'retry thrice'" in out and "'Sends requests.'" in out and "00000" not in out


def test_a_feature_name_that_matches_several_lists_them_with_their_ids(tmp_path):
    root, cd = seeded(tmp_path)
    st = client.read(cd).store
    hits = [h.id for h in st.headings() if "n" in tools.title(st, h.id).lower()]
    assert len(hits) > 1
    r = runner.invoke(app, ["handoff", "n", "--root", str(root)])
    assert r.exit_code == 1 and f"{len(hits)} features match 'n'" in r.output, r.output
    for fid in hits:
        assert f"  {fid}  {tools.title(st, fid)}" in r.output


def test_a_realize_pass_ends_with_its_time_and_cost(tmp_path, monkeypatch):
    """`claude -p` with a JSON result, and the SDK's result message: both
    passes end with one line that says what the run cost."""
    from codoc.claude import autorealize
    from codoc.claude.sdk_realize import RealizeMonitor
    claude = tmp_path / "claude"
    claude.write_text("#!/bin/sh\necho '{\"result\": \"Built the retry.\", \"is_error\": false, \"total_cost_usd\": 1.03}'\n")
    claude.chmod(0o755)
    monkeypatch.setattr(autorealize, "find_claude", lambda: str(claude))
    said = []
    assert autorealize.run_cli(str(tmp_path), "acceptEdits", printer=said.append) == 0
    assert said[0] == "Built the retry." and said[1].startswith("  ✓ done · ") and said[1].endswith(" · $1.03")
    monitor = RealizeMonitor(str(tmp_path), str(tmp_path / ".codoc"), printer=said.append, tty=False)
    monitor.handle_message(type("ResultMessage", (), {"is_error": False, "result": "", "total_cost_usd": 0.42})())
    assert monitor.summary().endswith(" · $0.42")
