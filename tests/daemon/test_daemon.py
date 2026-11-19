"""The daemon's per-batch routing with injected index and LLM."""
import json
import os
import shutil

import pytest

from codoc.calculus import ops as K
from codoc.calculus.doc import PARAGRAPH
from codoc.calculus.ids import START
from codoc.daemon.daemon import Daemon
from codoc.workspace.epoch import read_activity, write_activity
from codoc.index.changes import ChangeSet, ChunkRef
from codoc.workspace import Workspace
from codoc.workspace import log as L
from codoc.workspace import inbox as I
from codoc.workspace.inbox import Submission
from tests.helpers import handoff, seed_loop, T


def make(tmp_path, changesets: list):
    root = tmp_path / "repo"
    (root / ".codoc").mkdir(parents=True)
    calls: list[set[str]] = []

    def changeset(root_dir, codoc_dir, files):
        calls.append(set(files))
        return changesets.pop(0) if changesets else ChangeSet()

    d = Daemon(root, changeset=changeset, propose=lambda *a, **k: [], printer=lambda *_: None)
    seed_loop(d.ws)
    return d, calls


def test_code_change_reflects_and_publishes(tmp_path):
    d, calls = make(tmp_path, [ChangeSet(modified=[ChunkRef("c.py::send", "f2", "t1")])])
    (d.root / "c.py").write_text("def send(): ...")
    note = d.process_batch([str(d.root / "c.py")])
    assert "reflect" in note and calls == [{"c.py"}]
    assert d.ws.store.bindings["c.py::send"].fingerprint == "f2"
    assert (d.codoc_dir / "tree.codoc").read_text().split("\n\n", 1)[1].startswith("# Client")
    proj = json.loads((d.codoc_dir / "projection.json").read_text())
    assert proj["phases"] == {"h1": "drifted"}
    status = json.loads((d.codoc_dir / "status.json").read_text())
    assert status["state"] == "synced"


def test_open_epoch_holds_code_until_it_closes(tmp_path):
    d, calls = make(tmp_path, [ChangeSet(modified=[ChunkRef("c.py::send", "f3", "t1")])])
    act = d.codoc_dir / "activity.json"
    write_activity(d.codoc_dir, {"version": 1, "epoch": {"id": "ep-1", "open": True}, "touched": ["c.py"]})
    assert "opened" in d.process_batch([str(act)])
    (d.root / "c.py").write_text("x")
    assert "deferring" in d.process_batch([str(d.root / "c.py")]) and calls == []
    data = read_activity(d.codoc_dir)
    data["epoch"]["open"] = False
    write_activity(d.codoc_dir, data)
    note = d.process_batch([str(act)])
    assert "closed" in note and "reflect" in note and calls == [{"c.py"}]


def test_a_daemon_started_during_an_agent_turn_reflects_the_turn_when_it_closes(tmp_path):
    """The window reloads (the extension stops its daemon and starts a new
    one) while a coding agent is mid-turn. The new daemon reads the open epoch
    at its first batch, so the stop hook's close runs the pass over every file
    the turn touched, and the hand-off it built settles."""
    from codoc.calculus.derive import queued
    d, calls = make(tmp_path, [])
    handoff(d.ws, "h1", actor="human:local")
    write_activity(d.codoc_dir, {"version": 1, "epoch": {"id": "ep-1", "open": True}, "touched": ["c.py"]})
    d.process_batch([str(d.codoc_dir / "activity.json")])
    d.ws.close()

    again = Daemon(d.root, changeset=lambda _r, _c, files: calls.append(set(files)) or ChangeSet(
        modified=[ChunkRef("c.py::send", "f9", "t1")]), propose=lambda *a, **k: [], printer=lambda *_: None)
    again.start()
    assert "opened" in again.process_batch([])
    data = read_activity(again.codoc_dir)
    data["epoch"]["open"] = False
    write_activity(again.codoc_dir, data)
    assert "closed" in again.process_batch([str(again.codoc_dir / "activity.json")])
    assert calls == [{"c.py"}] and queued(again.ws.store) == []


def test_the_daemons_passes_write_in_the_workspace_language(tmp_path):
    """The reflect pass and the realize queue are written in the language
    `config.json` names, not in English."""
    from codoc.workspace.config import write_config
    asked: list = []
    d, _ = make(tmp_path, [ChangeSet(added=[ChunkRef("c.py::retry", "f2", "t2", "def retry(): ...")])])
    d._propose = lambda *a, **k: asked.append(k["doc_language"].code) or []
    write_config(d.codoc_dir, doc_language="ja")
    handoff(d.ws, "h1", actor="human:local")
    (d.root / "c.py").write_text("def retry(): ...")
    d.process_batch([str(d.root / "c.py")])
    assert asked == ["ja"] and "Japanese" in (d.codoc_dir / "realize.md").read_text()


def test_inbox_ops_are_drained_and_acked(tmp_path):
    d, _ = make(tmp_path, [])
    t = d.ws.store.blocks["p1"].text()
    I.submit(d.codoc_dir, "ide", [Submission(cid="a1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                             payload={"after": list(t.last_live_ref()), "text": " Typed."})])
    note = d.process_batch([str(I.inbox_dir(d.codoc_dir) / "ide.jsonl")])
    assert "1 client op" in note
    assert d.ws.store.blocks["p1"].text().live_text().endswith(" Typed.")
    assert (I.inbox_dir(d.codoc_dir) / "ide.ack.jsonl").exists()
    status = json.loads((d.codoc_dir / "status.json").read_text())
    assert status["state"] == "edited"


def test_a_batch_with_no_paths_drains_what_queued_while_no_daemon_ran(tmp_path):
    """Startup and `codoc sync` call process_batch([]). An edit a client
    appended while nothing was watching has no file event to announce it."""
    d, _ = make(tmp_path, [])
    t = d.ws.store.blocks["p1"].text()
    I.submit(d.codoc_dir, "ide", [Submission(cid="q1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                             payload={"after": list(t.last_live_ref()), "text": " Queued."})])
    assert "1 client op" in d.process_batch([])
    assert d.ws.store.blocks["p1"].text().live_text().endswith(" Queued.")
    assert "client op" not in d.process_batch([])          # drained once, from the saved offset


def test_a_starting_daemon_reads_what_a_daemonless_writer_wrote_after_it_built_its_store(tmp_path):
    """The daemon builds its store, then writes its pidfile. A write that
    applied between the two is read when it starts."""
    from codoc.daemon import client
    d, _ = make(tmp_path, [])
    last = list(d.ws.store.blocks["p1"].text().live_refs())[-1]
    client.write(d.codoc_dir, client="cli", bodies=[{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                                                    "payload": {"after": list(last), "text": " Between."}}])
    client.release(d.codoc_dir)
    d.start()
    assert d.ws.store.blocks["p1"].text().live_text().endswith(" Between.")


def test_a_daemonless_writer_starts_a_new_session_when_head_moves(tmp_path, monkeypatch):
    """The MCP server writes as the applier when no daemon runs. After a commit
    its session file may be in the repo, so the next write goes to a new file."""
    from codoc.daemon import client
    from codoc.workspace import Workspace

    head = {"sha": "a" * 40}
    monkeypatch.setattr(client, "head", lambda _root: (head["sha"], "main"))
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed_loop(ws); ws.close()

    def type_(words: str) -> str:
        last = list(client.read(cd).store.blocks["p1"].text().live_refs())[-1]
        client.write(cd, client="cli", bodies=[{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                                                "payload": {"after": list(last), "text": words}}])
        return client._OWNED[cd].session

    try:
        first, again = type_(" A."), type_(" B.")
        head["sha"] = "b" * 40                      # a commit
        after = type_(" C.")
    finally:
        client.release(cd)
    assert first == again != after
    fresh = Workspace(cd, readonly=True)
    assert fresh.store.blocks["p1"].text().live_text().endswith(" A. B. C.")
    fresh.close()


def test_a_warm_publisher_writes_what_a_cold_one_would(tmp_path):
    """The per-block memo is a pure-function cache: every key is a counter the
    store advances when the answer can change (docs/model.md, Cache keys)."""
    import json
    import random

    from codoc.calculus.doc import PARAGRAPH
    from codoc.calculus.ids import START
    from codoc.daemon.publish import Publisher
    from codoc.workspace import Workspace

    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    warm = Publisher(tmp_path / ".codoc", str(tmp_path))
    rng = random.Random(4)
    last = None
    for step in range(60):
        t = ws.store.blocks["p1"].text()
        r = rng.random()
        if r < 0.3:
            ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                        "payload": {"after": list(t.last_live_ref()), "text": f" w{step}"}}], actor="human:local")
        elif r < 0.45 and t.live_len() > 3:
            ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                        "payload": {"kind": "bold", "attrs": {}, "start": list(t.ref_at(0)), "end": list(t.ref_at(3)),
                                    "on": rng.random() < 0.5}}], actor="human:local")
        elif r < 0.6:
            last = ws.open_plan(actor="loop", kind="edit", target="p1")
            ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                        "payload": {"after": list(t.last_live_ref()), "text": " proposed"}}], actor="loop", plan=last)
        elif r < 0.7 and last:
            ws.submit([{"kind": K.PLAN_ACCEPT if rng.random() < 0.5 else K.PLAN_REJECT,
                        "target": {"pid": last}, "payload": {}}], actor="human:local")
            last = None
        elif r < 0.8:
            bid = f"b{step}"
            ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": bid}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
                       {"kind": K.TEXT_INSERT, "target": {"bid": bid, "path": "text"}, "payload": {"after": START, "text": f"para {step}"}}],
                      actor="loop")
        elif r < 0.9:
            ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor="human:local")
        else:
            ws.submit([{"kind": K.BIND_REFRESH, "target": {"fid": "h1"},
                        "payload": {"symbol": "c.py::send", "fingerprint": f"f{step}"}}], actor="loop")
        got = warm.publish(ws.store, force=True)
        warm_files = {f: (tmp_path / ".codoc" / f).read_text() for f in ("projection.json", "tree.codoc")}
        cold = Publisher(tmp_path / ".codoc", str(tmp_path))
        assert cold.phases(ws.store) == warm.phases(ws.store), step
        assert json.loads(warm_files["projection.json"]) == cold.projection(ws.store, cold.phases(ws.store)), step
        assert warm_files["tree.codoc"] == cold.export(ws.store), step
        assert got["state"] == cold.publish(ws.store, force=True)["state"], step


def test_derive_builds_the_projection_the_publisher_writes(tmp_path):
    """One shape for every caller. `claims` carries only what the editor cannot
    read off a block's runs and `suggest_del`: the code channel and an open
    plan's mark toggles."""
    from codoc.calculus.derive import projection
    from codoc.daemon.publish import publish

    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    end = lambda: list(ws.store.blocks["p1"].text().last_live_ref())   # noqa: E731
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": end(), "text": " Typed."}}], actor="human:local")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": end(), "text": " Loop."}}], actor="loop")
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": end(), "text": " Maybe."}},
               {"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": "bold", "attrs": {}, "start": list(t.ref_at(0)), "end": list(t.ref_at(6)), "on": True}}],
              actor="loop", plan=pid)
    publish(tmp_path / ".codoc", ws.store, root_dir=str(tmp_path))
    published = json.loads((tmp_path / ".codoc" / "projection.json").read_text())
    assert published == json.loads(json.dumps(projection(ws.store)))
    assert published["bindings"] == {"by_feature": {"h1": ["c.py::send"]}}
    assert [(c["channel"], c["stage"], c["text"]) for c in published["claims"]["h1"]] == [
        ("human", "unsent", " Typed."), ("plan", "open", " Maybe."), ("plan", "marked", "bold:Retries"),
        ("code", "added", " Loop.")]


def test_the_daemon_wakes_for_the_files_the_index_reads(tmp_path):
    """What is code is the index's answer: a file a language adapter parses,
    outside the index's excludes."""
    d, _ = make(tmp_path, [])
    rel = ["a.mts", "src/b.py", "dist/c.ts", "d.go", "e.js", "node_modules/f/g.ts", "h.d.ts", ".venv/i.py"]
    assert {d.code_path(str(d.root / p)) for p in rel} - {None} == {"a.mts", "src/b.py"}


def test_projection_carries_the_realizing_lease_and_publish_skips_unchanged(tmp_path):
    root = tmp_path / "repo"
    (root / ".codoc").mkdir(parents=True)
    d = Daemon(root, changeset=lambda *a: ChangeSet(), propose=lambda *a, **k: [], printer=lambda *_: None)
    seed_loop(d.ws)
    t = d.ws.store.blocks["p1"].text()
    d.ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Do it."}}], actor="human:local")
    handoff(d.ws, "h1", actor="human:local")
    d.process_batch([])
    assert json.loads((d.codoc_dir / "status.json").read_text())["state"] == "queued"
    (d.codoc_dir / "status.json").write_text(json.dumps({"state": "realizing", "version": 2}))
    d.process_batch([])
    proj = json.loads((d.codoc_dir / "projection.json").read_text())
    assert proj["phases"] == {"h1": "realizing"}
    stamp = (d.codoc_dir / "projection.json").stat().st_mtime_ns
    assert d.process_batch([]) == "" and (d.codoc_dir / "projection.json").stat().st_mtime_ns == stamp


def test_every_publish_reads_the_lease_and_none_renews_it(tmp_path):
    """A one-shot publish (the CLI, a daemonless writer) shows a feature another
    pass is building as `realizing`, as the daemon's does, and leaves the
    lease's file alone, so a pass that died lets it lapse."""
    from codoc.daemon.publish import publish
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    handoff(ws, "h1", actor="human:local")
    publish(ws.codoc_dir, ws.store, root_dir=str(tmp_path))
    status = ws.codoc_dir / "status.json"
    status.write_text(json.dumps({"state": "realizing", "version": 2}))
    stamp = status.stat().st_mtime_ns
    publish(ws.codoc_dir, ws.store, root_dir=str(tmp_path))
    assert json.loads((ws.codoc_dir / "projection.json").read_text())["phases"] == {"h1": "realizing"}
    assert status.stat().st_mtime_ns == stamp
    ws.close()


def test_head_change_rebuilds_from_the_logs_on_disk(tmp_path):
    root = tmp_path / "repo"
    (root / ".codoc").mkdir(parents=True)
    d = Daemon(root, changeset=lambda *a: ChangeSet(), propose=lambda *a, **k: [], printer=lambda *_: None)
    seed_loop(d.ws)
    d.process_batch([])
    # A peer's session file arrives (a pull) and HEAD moves.
    other = Workspace(d.codoc_dir, session="s-peer")
    t = other.store.blocks["p1"].text()
    other.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " From a peer."}}], actor="human:local")
    other.close()
    d.ws.head = ("moved", "main")
    note = d.process_batch([])
    assert "rebuilt" in note and T(d.ws.store, "p1").endswith(" From a peer.")


LANDS = {
    # the pull request's branch, checked out to review it: nothing merged
    "checkout the PR branch": ([["checkout", "-q", "codoc/pr"]], False),
    # a local branch that pulls the PR again as the hub pushes to it
    "re-pull the PR branch": ([["checkout", "-qb", "review"], ["merge", "-q", "--ff-only", "codoc/pr"]], False),
    "detached HEAD": ([["checkout", "-q", "--detach"], ["merge", "-q", "--ff-only", "codoc/pr"]], False),
    # `git pull --rebase` on the base with an unpushed commit: the new HEAD does not descend from the old
    "rebase pull on the base": ([["commit", "-qam", "local", "--allow-empty"], ["rebase", "-q", "codoc/pr"]], True),
    "merge on the base": ([["merge", "-q", "--no-ff", "--no-edit", "codoc/pr"]], True),
    "switch from a feature branch to a base that has the merge":
        ([["checkout", "-qb", "feat"], ["commit", "-q", "--allow-empty", "-m", "feat"],
          ["branch", "-f", "main", "codoc/pr"], ["checkout", "-q", "main"]], True),
}


@pytest.mark.parametrize("case", LANDS)
def test_a_handoff_trailer_counts_on_the_branch_its_base_trailer_names(tmp_path, monkeypatch, case):
    """A commit names its hand-off in `Codoc-Handoff` and the branch it must
    land on in `Codoc-Base`. The daemon counts it from the commits HEAD moved
    over only while HEAD is on that branch, whatever the move (a merge, a
    rebase, a checkout)."""
    import subprocess
    from codoc.index.git import BASE_TRAILER, HANDOFF_TRAILER
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
    (root / ".gitignore").write_text(".codoc/\n")
    (root / "c.py").write_text("def send(): ...\n")
    git("add", "-A")
    git("commit", "-qm", "init")
    fps = (f"f{i}" for i in range(2, 20))
    d = Daemon(root, changeset=lambda *a: ChangeSet(modified=[ChunkRef("c.py::send", next(fps), "t1")]),
               propose=lambda *a, **k: [], printer=lambda *_: None)
    seed_loop(d.ws)
    ho = handoff(d.ws, "h1", actor="human:local")
    git("checkout", "-qb", "codoc/pr")
    (root / "c.py").write_text("def send(): retry()\n")
    git("commit", "-qam", "codoc: Client", "-m", f"{HANDOFF_TRAILER}: {ho.id}\n{BASE_TRAILER}: main")
    git("checkout", "-q", "main")
    steps, lands = LANDS[case]
    for step in steps:
        git(*step)
        d.process_batch([str(root / "c.py")])
    assert queued(d.ws.store) == ([] if lands else ["h1"])
    assert [o.caused_by for o in d.ws.store.ops if o.kind == K.FEATURE_REALIZED] == ([ho.id] if lands else [])


def test_an_inbox_file_no_actor_owns_is_named_once(tmp_path):
    """An older MCP server wrote `mcp.jsonl`, which maps to no actor and is
    never drained. The daemon says so once, naming the file."""
    d, _ = make(tmp_path, [])
    said: list[str] = []
    d.say = said.append
    I.submit(d.codoc_dir, "mcp", [Submission(cid="m1", kind=K.BIND_ATTACH, target={"fid": "h1"},
                                             payload={"symbol": "c.py::a"})])
    for _ in range(2):
        assert "client op" not in d.process_batch([str(I.inbox_dir(d.codoc_dir) / "mcp.jsonl")])
    assert len(said) == 1 and "inbox/mcp.jsonl" in said[0] and "MCP server" in said[0]


def test_a_plan_on_a_deleted_heading_is_not_offered(tmp_path):
    root = tmp_path / "repo"
    (root / ".codoc").mkdir(parents=True)
    d = Daemon(root, changeset=lambda *a: ChangeSet(), propose=lambda *a, **k: [], printer=lambda *_: None)
    seed_loop(d.ws)
    pid = d.ws.open_plan(actor="loop", kind="edit", target="h1")
    t = d.ws.store.blocks["p1"].text()
    d.ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Maybe."}}], actor="loop", plan=pid)
    d.ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor="human:local")
    d.process_batch([])
    st = json.loads((d.codoc_dir / "status.json").read_text())
    proj = json.loads((d.codoc_dir / "projection.json").read_text())
    assert proj["blocks"] == [] and proj["plans"] == [] and st["state"] == "synced"


def test_rebuild_with_equal_pos_publishes_fresh_files(tmp_path):
    root = tmp_path / "repo"
    (root / ".codoc").mkdir(parents=True)
    d = Daemon(root, changeset=lambda *a: ChangeSet(), propose=lambda *a, **k: [], printer=lambda *_: None)
    seed_loop(d.ws)
    d.process_batch([])
    other = tmp_path / "other" / ".codoc"
    ws = Workspace(other)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": "heading", "level": 1, "after": START}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h1", "path": "text"}, "payload": {"after": START, "text": "Server"}},
               {"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": "h1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "Listens forever."}},
               {"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"symbol": "s.py::serve"}}], actor="loop")
    ws.close()
    d.ws.close()
    shutil.rmtree(d.codoc_dir / "log")
    shutil.copytree(other / "log", d.codoc_dir / "log")
    d.ws.head = ("other", "other-branch")
    d.process_batch([])
    assert T(d.ws.store, "p1") == "Listens forever." and "Listens forever." in (d.codoc_dir / "tree.codoc").read_text()


def test_an_agents_citing_binding_op_settles_its_handoff_with_no_epoch(tmp_path):
    """Cursor or Codex write through the MCP tools and run no Claude Code hooks,
    so no agent epoch ever closes. The binding op their `codoc_reflect` lands
    with the hand-off as `caused_by` is the evidence, and the daemon settles on
    it after the drain."""
    from codoc.calculus.derive import queued
    d, calls = make(tmp_path, [])
    ho = handoff(d.ws, "h1", actor="human:local")
    I.submit(d.codoc_dir, "agent-cursor", [Submission(
        cid="c1", kind=K.BIND_ATTACH, target={"fid": "h1"}, payload={"symbol": "c.py::retry"},
        caused_by=ho.id)])
    d.process_batch([str(I.inbox_dir(d.codoc_dir) / "agent-cursor.jsonl")])
    st = d.ws.store
    assert queued(st) == [] and calls == [] and not d.state.epoch_open
    attach = next(o for o in st.ops if o.kind == K.BIND_ATTACH and o.caused_by == ho.id)
    realized = [o for o in st.ops if o.kind == K.FEATURE_REALIZED]
    assert attach.actor == "agent:cursor"
    assert [(o.actor, o.caused_by, o.payload["files"]) for o in realized] == [("loop", ho.id, ["c.py"])]


def test_binding_ops_that_do_not_cite_the_latest_handoff_settle_nothing(tmp_path):
    """Only an op's recorded `caused_by` decides: an attach that cites nothing,
    or cites a hand-off the person has since superseded, is not evidence."""
    from codoc.calculus.derive import queued
    d, _ = make(tmp_path, [])
    old = handoff(d.ws, "h1", actor="human:local")
    handoff(d.ws, "h1", actor="human:local", asked="and log each retry")
    I.submit(d.codoc_dir, "agent-cursor", [
        Submission(cid="c1", kind=K.BIND_ATTACH, target={"fid": "h1"}, payload={"symbol": "c.py::a"}),
        Submission(cid="c2", kind=K.BIND_ATTACH, target={"fid": "h1"}, payload={"symbol": "c.py::b"},
                   caused_by=old.id)])
    assert "2 client op" in d.process_batch([str(I.inbox_dir(d.codoc_dir) / "agent-cursor.jsonl")])
    assert queued(d.ws.store) == ["h1"]
    assert not [o for o in d.ws.store.ops if o.kind == K.FEATURE_REALIZED]


def test_a_citing_attach_settles_nothing_while_an_agent_epoch_is_open_and_settles_at_its_close(tmp_path):
    """An open epoch is a build in progress: the hand-off stays queued, so the
    agent's later amends still land as plans. The close runs the same scan."""
    from codoc.calculus.derive import queued
    d, _ = make(tmp_path, [])
    ho = handoff(d.ws, "h1", actor="human:local")
    act = d.codoc_dir / "activity.json"
    write_activity(d.codoc_dir, {"version": 1, "epoch": {"id": "ep-1", "open": True}, "touched": []})
    assert "opened" in d.process_batch([str(act)])
    I.submit(d.codoc_dir, "agent-claude-code", [Submission(
        cid="c1", kind=K.BIND_ATTACH, target={"fid": "h1"}, payload={"symbol": "c.py::retry"},
        caused_by=ho.id)])
    d.process_batch([str(I.inbox_dir(d.codoc_dir) / "agent-claude-code.jsonl")])
    assert queued(d.ws.store) == ["h1"]
    data = read_activity(d.codoc_dir)
    data["epoch"]["open"] = False
    write_activity(d.codoc_dir, data)
    assert "closed" in d.process_batch([str(act)])
    assert queued(d.ws.store) == []
    assert [o.caused_by for o in d.ws.store.ops if o.kind == K.FEATURE_REALIZED] == [ho.id]


def test_a_batch_does_not_wake_the_daemon_for_what_it_wrote(tmp_path):
    """The daemon writes its session file, and acks, offsets.json and
    atomic_write's tmp files into the inbox. Waking on them made every batch
    start the next, forever; and a drain with nothing new rewrites nothing."""
    d, _ = make(tmp_path, [])
    inbox = I.inbox_dir(d.codoc_dir)
    keep = d.wakes
    t = d.ws.store.blocks["p1"].text()
    I.submit(d.codoc_dir, "ide", [Submission(cid="w1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                             payload={"after": list(t.last_live_ref()), "text": " Typed."})])
    assert keep(None, str(inbox / "ide.jsonl"))

    def stamps():
        return {p: p.stat().st_mtime_ns for p in d.codoc_dir.rglob("*") if p.is_file()}
    was = stamps()
    assert "1 client op" in d.process_batch([str(inbox / "ide.jsonl")])
    now = stamps()
    written = [p for p in now if was.get(p) != now[p]] + [inbox / ".offsets.json.x1.tmp"]
    assert (inbox / "offsets.json") in written and not [p for p in written if keep(None, str(p))]
    assert d.process_batch([]) == "" and stamps() == now


def test_no_log_names_the_machine(tmp_path, monkeypatch):
    """A tracked log's clock node is its writer's session id, for the daemon,
    a daemonless writer and a rotated session alike: never the hostname."""
    import socket
    from codoc.daemon import client
    from codoc.workspace.clock import parse
    monkeypatch.setattr(socket, "gethostname", lambda: "alices-laptop.local")
    d, _ = make(tmp_path, [])
    d.ws.rotate_session()
    handoff(d.ws, "h1", actor="human:local")
    client.write(d.codoc_dir, client="cli", bodies=[{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}])
    client.release(d.codoc_dir)
    d.ws.close()
    files = [L.read_session(p) for p in L.list_sessions(d.codoc_dir)]
    assert len(files) == 3
    for f in files:
        assert "alices-laptop" not in f.path.read_text()
        assert {parse(o.at)[2] for o in f.ops} == {f.session}


def test_the_daemon_prints_what_a_batch_did_and_nothing_for_a_no_op(tmp_path, monkeypatch):
    """A hook's write to activity.json starts a batch that does nothing, and
    it prints nothing. The batch a start runs drains what queued while no
    daemon ran, and prints it like any other."""
    import atexit
    import signal
    import watchfiles
    d, _ = make(tmp_path, [])
    said: list[str] = []
    d.say = said.append
    I.submit(d.codoc_dir, "ide", [Submission(cid="q1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                             payload={"after": list(d.ws.store.blocks["p1"].text().last_live_ref()),
                                                      "text": " Queued."})])
    monkeypatch.setattr(watchfiles, "watch", lambda *a, **k: iter([{(2, str(d.codoc_dir / "activity.json"))}]))
    monkeypatch.setattr(signal, "signal", lambda *a: None)
    monkeypatch.setattr(atexit, "register", lambda *a: None)
    d.run()
    d.ws.close()
    assert [line for line in said if line.startswith("▸")] == ["▸ 1 client op(s)"]


def test_a_daemon_started_after_a_turn_went_silent_reflects_that_turn(tmp_path):
    """A turn that touched c.py was killed without its hooks while no daemon
    ran. The daemon that starts later closes the stale epoch and runs the
    pass over what that turn touched, as if it had seen the turn open."""
    import time
    root = tmp_path / "repo"
    (root / ".codoc").mkdir(parents=True)
    calls: list[set[str]] = []

    def changeset(_root, _codoc, files):
        calls.append(set(files))
        return ChangeSet(modified=[ChunkRef("c.py::send", "f2", "t1")])
    write_activity(root / ".codoc", {"version": 1, "epoch": {"id": "ep-1", "open": True}, "touched": ["c.py"]})
    old = time.time() - 2000
    os.utime(root / ".codoc" / "activity.json", (old, old))
    d = Daemon(root, changeset=changeset, propose=lambda *a, **k: [], printer=lambda *_: None)
    assert "recovered a dead agent session" in d.process_batch([])
    assert read_activity(root / ".codoc")["epoch"]["open"] is False and calls == [{"c.py"}]


def test_a_lease_that_names_its_process_holds_while_the_process_lives(tmp_path):
    """A `claude -p` pass can spend longer than the lease's window on one
    hand-off without writing anything. Its lease names its process, so it
    holds for as long as that process runs, and lapses when it exits."""
    import subprocess
    import sys
    import time
    from codoc.daemon.publish import REALIZING_LEASE_SECONDS, realizing_is_fresh, write_realizing
    cd = tmp_path / ".codoc"
    cd.mkdir()
    (cd / "realize.md").write_text("### ⟨d:s:1⟩ Retry\n")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        write_realizing(cd, detail="building", owner="o1", pid=child.pid)
        later = time.time() + REALIZING_LEASE_SECONDS + 60
        assert realizing_is_fresh(cd, now=later)
    finally:
        child.kill()
        child.wait()
    assert not realizing_is_fresh(cd)

