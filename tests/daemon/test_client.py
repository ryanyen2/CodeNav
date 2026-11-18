"""Writers with no daemon: the applier lock, a held workspace catching up
with another process, and the ingest answer on both paths."""
import json
from pathlib import Path

from codoc.calculus import ops as K
from codoc.workspace import Workspace
from codoc.workspace import log as L
from codoc.workspace.snapshot import write_snapshot
from tests.helpers import T, refs, seed


def test_ingest_of_identical_text_reports_the_same_thing_on_both_paths(tmp_path):
    """An ingest that changes nothing gives the agent the same answer with no
    daemon as through the inbox."""
    from codoc.daemon import client
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()
    direct = client.ingest(cd, client="agent-claude-code", bid="p1", path="text", base_text="Retries once.", new_md="Retries once.")
    client.release(cd)
    # simulate the daemon path: submit + drain + read the ack the way client.ingest does
    ws = Workspace(cd)
    L.submit(cd, "agent-claude-code", [L.Submission(cid="c1", kind="ingest", target={"bid": "p1", "path": "text"},
                                      payload={"base_text": "Retries once.", "new_md": "Retries once."})])
    ws.drain_inbox()
    ack = client.wait_for_acks(cd, "agent-claude-code", ["c1"], 0.2)[0]
    via_daemon = {"refused": ack.get("conflict"), "ops": [ack["op"]] if ack.get("op") else []}
    ws.close()
    assert direct["refused"] == via_daemon["refused"], (direct, via_daemon)


def test_a_second_daemonless_writer_does_not_lose_the_firsts_work(tmp_path):
    """With no daemon, a writer is the applier for one call, and the MCP
    server keeps its workspace between calls. `client.applying` takes an
    exclusive lock for the turn and rebuilds when another process's log grew,
    so a second writer's ops land and every publish describes both."""
    import subprocess
    import sys

    from codoc.daemon import client
    from codoc.workspace.layout import PROJECTION as PROJECTION_FILE
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()

    def here(text):
        r = refs(Workspace(cd, readonly=True), "p1")
        return client.write(cd, client="cli", bodies=[{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                                                       "payload": {"after": list(r[-1]), "text": text}}],
                            root_dir=str(tmp_path))

    assert here(" A.")[0]["conflict"] is None                  # this process is now the applier, and holds it
    other = subprocess.run([sys.executable, "-c", f"""
import sys; sys.path.insert(0, {str(Path.cwd())!r})
from codoc.daemon import client
from codoc.workspace import Workspace
ws = Workspace({str(cd)!r}, readonly=True)
r = list(ws.store.blocks["p1"].text().live_refs())
print(client.write({str(cd)!r}, client="cli", bodies=[{{"kind": "text.insert", "target": {{"bid": "p1", "path": "text"}},
      "payload": {{"after": list(r[-1]), "text": " B."}}}}], root_dir={str(tmp_path)!r}))
"""], capture_output=True, text=True, timeout=120)
    assert other.returncode == 0, other.stderr
    assert here(" C.")[0]["conflict"] is None                  # …and our next turn sees theirs
    proj = json.loads((cd / PROJECTION_FILE).read_text())
    text = next(e["fields"]["text"]["text"] for e in proj["blocks"] if e["id"] == "p1")
    assert " A." in text and " B." in text and " C." in text, text
    client.release(cd)


def test_a_fold_does_not_orphan_the_session_a_held_applier_is_still_writing(tmp_path):
    """A held daemonless applier (the MCP server between calls) has an open
    session file whose every op is settled. A snapshot archives only a file
    its writer closed, so the applier's next op goes to a file that is
    replayed."""
    from codoc.daemon import client
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()
    r = list(client.read(cd).store.blocks["p1"].text().live_refs())
    client.write(cd, client="cli", bodies=[{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                                            "payload": {"after": list(r[-1]), "text": " A."}}])
    held = client._OWNED[cd]
    with client.applying(cd) as w:                     # `codoc snapshot`'s own path
        assert write_snapshot(cd, w.store, w.clock) is not None
    r = list(held.store.blocks["p1"].text().live_refs())
    client.write(cd, client="cli", bodies=[{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                                            "payload": {"after": list(r[-1]), "text": " B."}}])
    client.release(cd)
    fresh = Workspace(cd, readonly=True)
    assert T(fresh.store, "p1") == "Retries once. A. B.", (
        T(fresh.store, "p1"), [p.name for p in L.list_sessions(cd)], [p.name for p in L.list_archived(cd)])
    fresh.close()


def test_a_held_appliers_reads_see_what_another_process_wrote(tmp_path):
    """`read()` catches a held workspace up when another process's log grew,
    as `applying()` does. Every MCP read, and the `base_text` its `reflect`
    computes, goes through it; a stale read would make the agent's next amend
    `stale_base`."""
    import subprocess
    import sys

    from codoc.daemon import client
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()
    r = list(client.read(cd).store.blocks["p1"].text().live_refs())
    client.write(cd, client="cli", bodies=[{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                                            "payload": {"after": list(r[-1]), "text": " A."}}])
    other = subprocess.run([sys.executable, "-c", f"""
import sys; sys.path.insert(0, {str(Path.cwd())!r})
from codoc.daemon import client
ws = client.read({str(cd)!r})
r = list(ws.store.blocks["p1"].text().live_refs())
client.write({str(cd)!r}, client="cli", bodies=[{{"kind": "text.insert", "target": {{"bid": "p1", "path": "text"}},
      "payload": {{"after": list(r[-1]), "text": " B."}}}}])
"""], capture_output=True, text=True, timeout=120)
    assert other.returncode == 0, other.stderr
    assert " B." in T(client.read(cd).store, "p1"), T(client.read(cd).store, "p1")
    client.release(cd)


def test_an_amends_new_block_cannot_carry_words_attributed_to_someone_else(tmp_path):
    """An amend's bodies are cleaned like any other submission: a `block.add`
    carrying snapshot `texts` does not land the owner's words for the agent."""
    from codoc.calculus.derive import attribution
    from codoc.daemon import client
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    owners = ws.store.by_id[refs(ws, "p1")[0][0]]
    ws.close()
    forged = {"segs": [{"op": owners.id, "k0": 500, "chars": "Approved by the owner.", "actor": "human:local",
                        "at": owners.at}], "marks": []}
    client.amend(cd, client="agent-claude-code", fid="h1", rewrites=[], bodies=[
        {"kind": K.BLOCK_ADD, "target": {"bid": "b-forged000001"},
         "payload": {"kind": "paragraph", "after": "p1", "texts": {"text": forged}}}])
    b = client.read(cd).store.blocks["b-forged000001"]
    assert b.text().live_text() == "" and not attribution(b.text())
    client.release(cd)


def test_a_writer_that_waited_for_the_lock_while_a_daemon_started_uses_its_inbox(tmp_path, monkeypatch):
    """A writer that saw no daemon and got the applier lock only after the
    daemon's start-up rebuild would apply ops the daemon never reads. It asks
    again under the lock and submits to the inbox."""
    import os
    from contextlib import contextmanager

    from codoc.daemon import client
    from codoc.workspace.layout import PIDFILE
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()
    r = refs(Workspace(cd, readonly=True), "p1")
    real = client.locked

    @contextmanager
    def a_daemon_starts_meanwhile(path):
        (cd / PIDFILE).write_text(str(os.getppid()))      # a live process that is not this one
        with real(path):
            yield
    monkeypatch.setattr(client, "locked", a_daemon_starts_meanwhile)
    got = client.write(cd, client="cli", wait_s=0, bodies=[{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                                                           "payload": {"after": list(r[-1]), "text": " X."}}])
    assert not got[0]["op"] and (L.inbox_dir(cd) / "cli.jsonl").exists()
    assert T(Workspace(cd, readonly=True).store, "p1") == "Retries once."
    client.release(cd)
