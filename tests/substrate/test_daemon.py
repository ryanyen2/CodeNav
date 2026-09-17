"""The daemon's per-batch routing with injected index and LLM."""
import json
import time

from codoc.calculus import ops as K
from codoc.loop.activity import write_activity, read_activity
from codoc.loop.diff import ChangeSet, ChunkRef
from codoc.substrate import log as L
from codoc.substrate.daemon import Daemon
from codoc.substrate.log import Submission

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
