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
from tests.helpers import ROWS, fake_file, fake_org, code_holds

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
    assert r.exit_code == 0 and "hand-off: 'retry thrice'" in r.output and "who wrote it:" in r.output
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
    code_holds(monkeypatch, "net/client.py::Client.retry")
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


def test_without_root_the_cli_finds_the_workspace_from_a_subdirectory(tmp_path, monkeypatch):
    """As the hooks and the MCP server do. An explicit --root is taken as given."""
    root, cd = seeded(tmp_path)
    sub = root / "src" / "pkg"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0 and "4 features" in r.output, r.output
    r = runner.invoke(app, ["status", "--root", str(sub)])
    assert r.exit_code == 1 and "No codoc workspace at" in r.output
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["status"]).exit_code == 1


def test_init_makes_its_workspace_where_it_is_told_and_leaves_the_enclosing_one_alone(tmp_path, monkeypatch):
    """A package in a monorepo may hold its own workspace: `codoc init`, with
    or without --root and with --force, never walks up to the parent's."""
    from codoc.cli import main
    root, cd = seeded(tmp_path)
    logs = sorted(p.name for p in (cd / "log").iterdir())
    monkeypatch.setattr(main, "_bootstrap", lambda *a, **k: type("R", (), {"summary": lambda self: "built"})())
    pkg, lib = root / "pkg", root / "lib"
    pkg.mkdir()
    lib.mkdir()
    r = runner.invoke(app, ["init", "--force", "--no-hooks", "--root", str(pkg)])
    assert r.exit_code == 0 and f"Note: {cd.resolve()} is a workspace too" in r.output, r.output
    monkeypatch.chdir(lib)
    assert runner.invoke(app, ["init", "--no-hooks"]).exit_code == 0
    assert (pkg / ".codoc").is_dir() and (lib / ".codoc").is_dir()
    assert sorted(p.name for p in (cd / "log").iterdir()) == logs and not (cd / "log.old").exists()


def test_history_says_when_what_and_for_which_handoff(tmp_path, monkeypatch):
    """A local time, not the clock's padded stamp; the symbol a binding names;
    the hand-off an op answers; and who wrote each run of words, as words."""
    import re
    root, cd = seeded(tmp_path)
    code_holds(monkeypatch, "net/client.py::Client.retry")
    runner.invoke(app, ["handoff", "HTTP client", "--root", str(root), "--asked", "retry thrice"])
    st = client.read(cd).store
    fid = next(h.id for h in st.headings() if tools.title(st, h.id) == "HTTP client")
    ho = st.watermarks(fid).handoffs[-1]
    assert tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": fid, "bindings": ["net/client.py::Client.retry"]}],
                         caused_by=ho, root_dir=str(root))["ok"]
    out = runner.invoke(app, ["history", "HTTP client", "--root", str(root)]).output
    assert re.search(rf"\n  \d{{4}}-\d\d-\d\d \d\d:\d\d:\d\d  agent:claude-code +"
                     rf"bound net/client.py::Client.retry  for hand-off d:{ho}\n", out), out
    assert "hand-off: 'retry thrice'" in out and "'Sends requests.'" in out and "00000" not in out


def test_history_is_one_row_per_change_so_a_plan_does_not_bury_the_handoff(tmp_path):
    """A reader thinks in gestures: a plan of ten bullets is one change, not
    the forty ops that wrote it, and `--limit` counts changes."""
    from codoc.calculus.derive import history as feature_history
    from codoc.daemon import reads
    root, cd = seeded(tmp_path)
    st = client.read(cd).store
    fid = next(h.id for h in st.headings() if tools.title(st, h.id) == "HTTP client")
    res = tools.propose(str(cd), ops=[{"kind": "amend", "feature_id": fid,
                                       "blocks": [{"text": f"- step {i} of the retry"} for i in range(10)]}],
                        note="steps", root_dir=str(root))
    pid = res["plan_id"]
    assert runner.invoke(app, ["accept", pid, "--root", str(root), "--no-build"]).exit_code == 0
    assert runner.invoke(app, ["handoff", fid, "--root", str(root), "--asked", "retry thrice"]).exit_code == 0
    assert len(feature_history(client.read(cd).store, fid)) > 15         # more ops than the default limit
    out = runner.invoke(app, ["history", fid, "--root", str(root)]).output
    assert f"plan {pid}: opened; 10 bullets added; 50 words added" in out and "hand-off: 'retry thrice'" in out, out
    rows = reads.read_history(cd, fid, 2)["changes"]
    assert [r["summary"] for r in rows] == [f"accepted plan {pid}", "hand-off: 'retry thrice'"] and "ops" not in rows[0]
    detail = runner.invoke(app, ["history", fid, "--root", str(root), "--limit", "1", "--ops"]).output
    assert "feature.handoff  'retry thrice'" in detail and "accepted plan" not in detail, detail


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


def test_auto_realize_starts_a_pass_once_per_handoff_and_backs_off_after_a_failure(tmp_path, monkeypatch):
    """The lease names the pass's pid and lapses when it exits, and the pass's
    own Stop hook wakes the daemon. A pass that ended with its hand-off still
    queued is not started again for it; one that failed is retried after a
    back-off that doubles."""
    import subprocess
    import sys
    from types import SimpleNamespace
    from codoc.claude import autorealize as A
    from codoc.claude import sdk_realize
    cd = tmp_path / ".codoc"
    cd.mkdir()
    queue = cd / "realize.md"
    queue.write_text("### ⟨d:s:1⟩ Retry\n")
    exits, started, now = [], [], [0.0]
    real = subprocess.Popen

    def fake_pass(argv, **kw):                   # a real process, so the lease names a pid that exits
        started.append(real([sys.executable, "-c", f"raise SystemExit({exits.pop(0)})"]))
        return started[-1]
    monkeypatch.setattr(A.subprocess, "Popen", fake_pass)
    monkeypatch.setattr(A, "find_claude", lambda: "/usr/bin/true")
    monkeypatch.setattr(sdk_realize, "sdk_available", lambda: False)
    run = A.auto_realizer(str(tmp_path), str(cd), clock=lambda: now[0])
    daemon = SimpleNamespace(state=SimpleNamespace(epoch_open=False))

    def batch():
        run(daemon)
        for proc in started:
            proc.wait()
    exits[:] = [0, 1, 1, 0]
    batch()                                      # s:1 queued: a pass starts and finishes
    batch()                                      # s:1 still queued: not started again
    assert len(started) == 1
    queue.write_text("### ⟨d:s:1⟩ Retry\n\n### ⟨d:s:2⟩ Back-off\n")
    batch()                                      # s:2 is new: a pass starts, and fails
    batch()
    assert len(started) == 2                     # within the back-off
    now[0] = A.FAILED_BACKOFF_S
    batch()                                      # retried; fails again, seen at the next batch
    batch()
    now[0] += A.FAILED_BACKOFF_S
    batch()
    assert len(started) == 3                     # the back-off doubled
    now[0] += A.FAILED_BACKOFF_S
    batch()
    assert len(started) == 4


def test_a_relative_root_is_made_absolute_once(tmp_path, monkeypatch):
    """`codoc realize --root repo` starts its child in the repo and names the
    repo to it; a relative root would be resolved a second time there."""
    import os
    import stat
    from pathlib import Path
    root, cd = seeded(tmp_path)
    runner.invoke(app, ["handoff", "HTTP client", "--root", str(root)])
    bindir, seen = tmp_path / "bin", tmp_path / "claude-cwd"
    bindir.mkdir()
    fake = bindir / "claude"
    fake.write_text(f"#!/bin/sh\npwd > {seen}\necho '{json.dumps({'result': 'ok'})}'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))     # the child imports this codoc
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["realize", "--engine", "cli", "--root", "repo"])
    assert r.exit_code == 0, r.output
    assert os.path.realpath(seen.read_text().strip()) == os.path.realpath(root)


def test_the_back_off_counts_from_when_the_failed_pass_ended(tmp_path, monkeypatch):
    """The pass's child records when it exited (`record_end`); the daemon may
    notice much later, or sooner, and the retry is timed from the exit."""
    import os
    import subprocess
    import sys
    from types import SimpleNamespace
    from codoc.claude import autorealize as A
    from codoc.claude import sdk_realize
    cd = tmp_path / ".codoc"
    (cd / "cache").mkdir(parents=True)
    (cd / "realize.md").write_text("### ⟨d:s:1⟩ Retry\n")
    assert A.record_end(cd, 3) == 3 and abs(A._ended(str(cd), os.getpid()) - __import__("time").time()) < 60
    ended = cd / A.PASS_ENDED
    started, real, now = [], subprocess.Popen, [0.0]
    child = ("import json, os, sys; open(sys.argv[1], 'w').write(json.dumps("
             "{'pid': os.getpid(), 'code': 1, 'at': 500.0})); raise SystemExit(1)")

    def fake_pass(argv, **kw):                   # a pass that ran until t=500, then failed
        started.append(real([sys.executable, "-c", child, str(ended)]))
        return started[-1]
    monkeypatch.setattr(A.subprocess, "Popen", fake_pass)
    monkeypatch.setattr(A, "find_claude", lambda: "/usr/bin/true")
    monkeypatch.setattr(sdk_realize, "sdk_available", lambda: False)
    run = A.auto_realizer(str(tmp_path), str(cd), clock=lambda: now[0])
    daemon = SimpleNamespace(state=SimpleNamespace(epoch_open=False))
    run(daemon)
    started[0].wait()
    now[0] = 500 + A.FAILED_BACKOFF_S - 1
    run(daemon)
    assert len(started) == 1                     # timed from the start, it would already retry
    now[0] = 500 + A.FAILED_BACKOFF_S
    run(daemon)
    assert len(started) == 2


def test_a_quiet_daemon_still_runs_its_batch_on_a_tick(tmp_path, monkeypatch):
    """Auto-realize's retry and the stale-epoch close run in a batch; with no
    file event the watch yields empty when its timeout (`TICK_MS`) runs out,
    and that is a batch too."""
    import atexit
    import signal
    import time
    import watchfiles
    from codoc.daemon import daemon as D
    root, cd = seeded(tmp_path)
    seen, ran = {}, []
    monkeypatch.setattr(signal, "signal", lambda *a: None)       # run() installs process-wide handlers
    monkeypatch.setattr(atexit, "register", lambda *a: None)
    monkeypatch.setattr(D, "TICK_MS", 20)

    def fake_watch(*paths, **kw):
        seen.update(kw)
        time.sleep(kw["rust_timeout"] / 1000)
        yield set()                              # the timeout, with no change
    monkeypatch.setattr(watchfiles, "watch", fake_watch)
    d = D.Daemon(root, cd, changeset=lambda *a: None, propose=lambda *a, **k: [], printer=lambda *_: None,
                 after_batch=lambda daemon: ran.append(1))
    d.run()
    d.ws.close()
    assert seen["yield_on_timeout"] and seen["rust_timeout"] == D.TICK_MS
    assert len(ran) == 2                         # the startup batch, then the tick


def test_history_rows_are_changes_in_the_order_they_happened(tmp_path):
    """An agent's amend of two paragraphs is one row; one reflect pass is one
    row; a later pass that joins the loop's open plan is a change of its own,
    after what happened in between."""
    from codoc.calculus import ops as K
    from codoc.calculus.ids import START
    from codoc.daemon import reads
    from codoc.index.changes import ChangeSet
    from codoc.index.graph import Graph
    from codoc.loops.edits import Compiler
    from codoc.loops.reflect import reflect
    from codoc.workspace import Workspace
    from tests.helpers import H, chunk, ins, seed_loop
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed_loop(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": "paragraph", "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"},
                "payload": {"after": START, "text": "Logs each failure to the console."}}], actor="loop")
    before = len(reads.read_history(cd, "h1", 100)["changes"])
    done = ws.amend("h1", rewrites=[("p1", "text", "Retries three times before giving up.", None),
                                    ("p2", "text", "Logs each failure to a file.", None)], actor="agent:claude-code")
    assert done.ops and not done.plan

    def propose(changes, subtree, titles, **kw):
        return [{"kind": "amend", "feature_id": "f1", "rationale": "send retries twice",
                 "blocks": [{"id": "p1", "text": "Retries twice before giving up."},
                            {"id": "p2", "text": "Logs each failure to stderr."}]}]
    res = reflect(ws, ChangeSet(modified=[chunk("c.py::send", "f2", "t1")]), Graph(), propose=propose)
    assert not res.plans and res.ops, res.summary()
    rows = reads.read_history(cd, "h1", 100)["changes"]
    assert [r["actor"] for r in rows[before:]] == ["agent:claude-code", "loop"], [r["summary"] for r in rows[before:]]
    first = Compiler(ws.store).compile({"kind": "amend", "feature_id": "h1", "blocks": [{"text": "- Retries on 503."}]})
    pid = ws.amend("h1", rewrites=[], bodies=first.bodies, actor="loop", structural=True).plan
    ins(ws, "h1", ws.store.blocks["h1"].text().last_live_ref(), " (HTTP)", actor=H)
    later = Compiler(ws.store).compile({"kind": "amend", "feature_id": "h1", "blocks": [{"text": "- Gives up after 3 tries."}]})
    newest = ws.amend("h1", rewrites=[], bodies=later.bodies, actor="loop", structural=True, join=pid).ops[-1]
    ws.close()
    rows = reads.read_history(cd, "h1", 3, ops=True)["changes"]
    assert [(r["actor"], r.get("plan")) for r in rows] == [("loop", pid), (H, None), ("loop", pid)]
    assert newest.id in [e["op"] for e in rows[-1]["ops"]] and rows[-1]["at"] > rows[-2]["at"] > rows[0]["at"]


def test_auto_realize_gives_up_on_a_handoff_after_three_failed_passes(tmp_path, monkeypatch):
    """A pass that fails after spending spends again: after three in a row the
    hand-off is left alone, once said, until a new hand-off is queued."""
    import subprocess
    import sys
    from types import SimpleNamespace
    from codoc.claude import autorealize as A
    from codoc.claude import sdk_realize
    cd = tmp_path / ".codoc"
    cd.mkdir()
    queue = cd / "realize.md"
    queue.write_text("### ⟨d:s:1⟩ Retry\n")
    started, said, real, now = [], [], subprocess.Popen, [0.0]

    def fake_pass(argv, **kw):
        started.append(real([sys.executable, "-c", "raise SystemExit(1)"]))
        return started[-1]
    monkeypatch.setattr(A.subprocess, "Popen", fake_pass)
    monkeypatch.setattr(A, "find_claude", lambda: "/usr/bin/true")
    monkeypatch.setattr(sdk_realize, "sdk_available", lambda: False)
    run = A.auto_realizer(str(tmp_path), str(cd), clock=lambda: now[0], printer=said.append)
    daemon = SimpleNamespace(state=SimpleNamespace(epoch_open=False))
    for _ in range(6):
        run(daemon)
        for proc in started:
            proc.wait()
        now[0] += 3600.0                         # past any back-off
    assert len(started) == A.MAX_FAILED_PASSES
    assert len(said) == 1 and "3 passes in a row failed" in said[0] and "d:s:1" in said[0], said
    queue.write_text("### ⟨d:s:1⟩ Retry\n\n### ⟨d:s:2⟩ Back-off\n")
    run(daemon)
    assert len(started) == A.MAX_FAILED_PASSES + 1


def test_auto_realize_counts_failed_passes_per_handoff(tmp_path, monkeypatch):
    """A hand-off queued while another keeps failing is not given up on with
    it: each gets three failed passes of its own."""
    import subprocess
    import sys
    from types import SimpleNamespace
    from codoc.claude import autorealize as A
    from codoc.claude import sdk_realize
    cd = tmp_path / ".codoc"
    cd.mkdir()
    queue = cd / "realize.md"
    queue.write_text("### ⟨d:s:1⟩ Retry\n")
    started, said, real, now = [], [], subprocess.Popen, [0.0]

    def fake_pass(argv, **kw):
        started.append(real([sys.executable, "-c", "raise SystemExit(1)"]))
        return started[-1]
    monkeypatch.setattr(A.subprocess, "Popen", fake_pass)
    monkeypatch.setattr(A, "find_claude", lambda: "/usr/bin/true")
    monkeypatch.setattr(sdk_realize, "sdk_available", lambda: False)
    run = A.auto_realizer(str(tmp_path), str(cd), clock=lambda: now[0], printer=said.append)
    daemon = SimpleNamespace(state=SimpleNamespace(epoch_open=False))

    def batch():
        run(daemon)
        for proc in started:
            proc.wait()
        now[0] += 3600.0                         # past any back-off
    batch()                                      # s:1 fails once
    queue.write_text("### ⟨d:s:1⟩ Retry\n\n### ⟨d:s:2⟩ Back-off\n")
    for _ in range(6):
        batch()
    assert len(started) == 4                     # s:1 is given up after 3, s:2 after its own 3
    assert [m.split(" on ")[1].split(" ")[0] for m in said] == ["d:s:1", "d:s:2"], said
