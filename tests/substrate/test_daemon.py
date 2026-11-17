"""The daemon's per-batch routing with injected index and LLM."""
import json
import time

from codoc.calculus import ops as K
from codoc.daemon.epoch import write_activity, read_activity
from codoc.index.changes import ChangeSet, ChunkRef
from codoc.workspace import log as L
from codoc.daemon.daemon import Daemon
from codoc.workspace.log import Submission

from tests.loops.test_reflect import seed


def make(tmp_path, changesets: list):
    root = tmp_path / "repo"
    (root / ".codoc").mkdir(parents=True)
    calls: list[set[str]] = []

    def changeset(root_dir, codoc_dir, files):
        calls.append(set(files))
        return changesets.pop(0) if changesets else ChangeSet()

    d = Daemon(root, changeset=changeset, propose=lambda *a, **k: [], printer=lambda *_: None)
    seed(d.ws)
    return d, calls


def test_code_change_reflects_and_publishes(tmp_path):
    d, calls = make(tmp_path, [ChangeSet(modified=[ChunkRef("c.py", "c.py::send", "f2", "t1")])])
    (d.root / "c.py").write_text("def send(): ...")
    note = d.process_batch([str(d.root / "c.py")])
    assert "reflect" in note and calls == [{"c.py"}]
    assert d.ws.store.bindings[("c.py", "c.py::send")].fingerprint == "f2"
    assert (d.codoc_dir / "tree.codoc").read_text().startswith("# Client")
    proj = json.loads((d.codoc_dir / "projection.json").read_text())
    assert proj["phases"] == {"h1": "drifted"}
    status = json.loads((d.codoc_dir / "status.json").read_text())
    assert status["state"] == "synced"


def test_open_epoch_holds_code_until_it_closes(tmp_path):
    d, calls = make(tmp_path, [ChangeSet(modified=[ChunkRef("c.py", "c.py::send", "f3", "t1")])])
    act = d.codoc_dir / "activity.json"
    write_activity(d.codoc_dir, {"version": 1, "epoch": {"id": "ep-1", "origin": "interactive", "open": True},
                                 "touched": {"c.py": {"symbols": [], "feature_ids": ["h1"], "mode": "write"}}, "recent": []})
    assert "opened" in d.process_batch([str(act)])
    (d.root / "c.py").write_text("x")
    assert "holding" in d.process_batch([str(d.root / "c.py")]) and calls == []
    data = read_activity(d.codoc_dir)
    data["epoch"]["open"] = False
    write_activity(d.codoc_dir, data)
    note = d.process_batch([str(act)])
    assert "closed" in note and "reflect" in note and calls == [{"c.py"}]


def test_inbox_ops_are_drained_and_acked(tmp_path):
    d, _ = make(tmp_path, [])
    t = d.ws.store.blocks["p1"].text()
    L.submit(d.codoc_dir, "ide", [Submission(cid="a1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                             payload={"after": list(t.last_live_ref()), "text": " Typed."})])
    note = d.process_batch([str(L.inbox_dir(d.codoc_dir) / "ide.jsonl")])
    assert "1 client op" in note
    assert d.ws.store.blocks["p1"].text().live_text().endswith(" Typed.")
    assert (L.inbox_dir(d.codoc_dir) / "ide.ack.jsonl").exists()
    status = json.loads((d.codoc_dir / "status.json").read_text())
    assert status["state"] == "edited"


def test_a_warm_publisher_writes_what_a_cold_one_would(tmp_path):
    """The per-block memo is a pure-function cache: every key is a counter the
    store advances when the answer can change (spec §6)."""
    import json
    import random
    from codoc.calculus.doc import PARAGRAPH
    from codoc.calculus.ids import START
    from codoc.daemon.publish import Publisher
    from codoc.workspace import Workspace

    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
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
                        "payload": {"file": "c.py", "symbol": "c.py::send", "fingerprint": f"f{step}"}}], actor="loop")
        got = warm.publish(ws.store, force=True)
        warm_files = {f: (tmp_path / ".codoc" / f).read_text() for f in ("projection.json", "tree.codoc")}
        cold = Publisher(tmp_path / ".codoc", str(tmp_path))
        assert cold.phases(ws.store) == warm.phases(ws.store), step
        assert json.loads(warm_files["projection.json"]) == cold.projection(ws.store, cold.phases(ws.store)), step
        assert warm_files["tree.codoc"] == cold.export(ws.store), step
        assert got["state"] == cold.publish(ws.store, force=True)["state"], step
