"""The command line and the agent's tools, end to end without an LLM or a daemon."""
import json

from typer.testing import CliRunner

from codoc.cli.main import app
from codoc.daemon import client
from codoc.daemon.publish import publish
from codoc.index.graph import Graph
from codoc.loops.bootstrap import bootstrap
from codoc.mcp import tools
from codoc.workspace import Workspace
from tests.helpers import ROWS, fake_file, fake_org

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


def test_status_handoff_history_and_verdicts_via_cli(tmp_path):
    root, cd = seeded(tmp_path)
    r = runner.invoke(app, ["status", "--root", str(root)])
    assert r.exit_code == 0 and "synced" in r.output and "4 features" in r.output
    r = runner.invoke(app, ["handoff", "HTTP client", "--root", str(root), "--asked", "retry thrice"])
    assert r.exit_code == 0 and "handed off" in r.output
    r = runner.invoke(app, ["status", "--root", str(root)])
    assert "queued" in r.output and (cd / "realize.md").read_text().count("Author asked: retry thrice") == 1
    r = runner.invoke(app, ["history", "HTTP client", "--root", str(root)])
    assert r.exit_code == 0 and "feature.handoff" in r.output and "loop[" in r.output
    # An agent proposes an amend; the person rejects it from the CLI.
    st = client.read(cd).store
    client_id = next(h.id for h in st.headings() if "HTTP client" in tools.title(st, h.id))
    body = st.feature_body(client_id)
    res = tools.propose(str(cd), ops=[{"kind": "amend", "feature_id": client_id,
                                       "blocks": [{"id": body[1].id, "text": "Sends requests over HTTP/2."}]}],
                        note="http2", root_dir=str(root))
    assert res["ok"]
    st = client.read(cd).store
    assert st.plans[res["plan_id"]].status == "open"
    assert "HTTP/2" in tools.description(st, client_id)          # live shows the proposal
    assert "HTTP/2" not in tools.description(st, client_id, "rejected")
    r = runner.invoke(app, ["reject", res["plan_id"], "--root", str(root)])
    assert r.exit_code == 0 and "rejected" in r.output
    assert "HTTP/2" not in tools.description(client.read(cd).store, client_id)


def test_reflect_attaches_and_repairs_and_closes_the_queue(tmp_path):
    root, cd = seeded(tmp_path)
    st = client.read(cd).store
    client_id = next(h.id for h in st.headings() if "HTTP client" in tools.title(st, h.id))
    runner.invoke(app, ["handoff", client_id, "--root", str(root)])
    ho = client.read(cd).store.watermarks(client_id).handoffs[-1]
    ctx = tools.read_context(str(cd), files=["net/client.py"])
    me = next(e for e in ctx["subtree"] if e["id"] == client_id)
    assert me["blocks"] and "written_by" in me and "phase" not in me
    res = tools.reflect(str(cd), ops=[
        {"kind": "attach", "feature_id": client_id, "bindings": ["net/client.py::Client.retry"]},
        {"kind": "amend", "feature_id": client_id, "blocks": [{"id": me["blocks"][0]["id"], "text": "Sends requests, three times if need be."}]},
    ], caused_by=ho, root_dir=str(root))
    assert res["ok"] and all(a["conflict"] is None for a in res["applied"])
    assert res["amended"][0]["refused"] == [] and res["amended"][0]["status"].startswith("plan:")  # queued: a plan
    st = client.read(cd).store
    assert ("net/client.py", "net/client.py::Client.retry") in st.bindings
    assert "three times" in tools.description(st, client_id)
    assert any(o.caused_by == ho and o.kind == "bind.attach" for o in st.ops)
    status = tools.read_status(str(cd))
    assert status["doc_language"]["code"] == "en"


def test_walkthrough_and_tree_reads(tmp_path):
    root, cd = seeded(tmp_path)
    t = tools.read_tree(str(cd))
    assert len(t["features"]) == 4 and all("blocks" in f for f in t["features"])
    fid = t["features"][0]["id"]
    w = tools.walkthrough(str(cd), question="where do requests go?", steps=[{"feature_id": fid, "note": "start here", "quote": "nope"}])
    assert w["ok"] and w["labels"] == ["1"] and w["unresolved_quotes"]
    assert tools.read_walkthrough_tool(str(cd))["walkthrough"]["question"] == "where do requests go?"
    assert tools.clear_walkthrough_tool(str(cd))["cleared"]


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
    from codoc.loops.realize import queued
    root, cd = seeded(tmp_path)
    st = client.read(cd).store
    fid = next(h.id for h in st.headings() if "HTTP client" in tools.title(st, h.id))
    body = st.feature_body(fid)
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


def test_no_build_takes_the_words_without_the_work(tmp_path):
    from codoc.loops.realize import queued
    root, cd = seeded(tmp_path)
    st = client.read(cd).store
    fid = next(h.id for h in st.headings() if "HTTP client" in tools.title(st, h.id))
    body = st.feature_body(fid)
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
    from codoc.loops.realize import queued
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


def test_a_daemon_that_starts_before_the_lock_gets_a_message_not_a_traceback(tmp_path, monkeypatch):
    """The commands that apply themselves learn about a daemon under the lock."""
    root, cd = seeded(tmp_path)
    monkeypatch.setattr(client, "daemon_running", lambda _cd: True)
    for cmd, says in (("rebuild", "A daemon is watching"), ("snapshot", "Stop the daemon"),
                      ("translate", "Stop the daemon")):
        r = runner.invoke(app, [cmd, "--root", str(root)] + (["--to", "fr"] if cmd == "translate" else []))
        assert r.exit_code == 1 and says in r.output, (cmd, r.output, r.exception)
