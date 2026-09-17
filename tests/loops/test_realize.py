"""Hand-off → queue → realization watermark (spec §3 watermarks, 02 §1)."""
from codoc.calculus import ops as K
from codoc.calculus.derive import phase
from codoc.graph.graph import Graph
from codoc.loop.diff import ChangeSet, ChunkRef
from codoc.loops.realize import handoff, queued, render_queue, settle, withdraw, write_queue
from codoc.loops.reflect import reflect
from codoc.substrate.workspace import Workspace

from tests.loops.test_reflect import seed


def test_handoff_queues_directive_and_reflect_under_it_realizes(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " Now retries **three times**."}}], actor="human:local")
    # The bold is a Focus line: mark it as the person.
    t = ws.store.blocks["p1"].text()
    start = t.ref_at(t.live_text().index("three")); end = t.ref_at(t.live_text().index("times") + 4)
    ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": "bold", "attrs": {}, "start": list(start), "end": list(end), "on": True}}], actor="human:local")
    assert phase(ws.store, "h1") == "edited"
    ho = handoff(ws, "h1", actor="human:local", asked="make the client retry three times")
    assert phase(ws.store, "h1") == "queued" and queued(ws.store) == ["h1"]
    md = render_queue(ws.store, root_dir="/repo")
    assert f"⟨d:{ho.id}⟩" in md and "Focus:" in md and "Author asked: make the client retry three times" in md
    assert write_queue(tmp_path / ".codoc", ws.store) is not None
    # The agent implements; the reflect pass cites the hand-off via caused_by.
    cs = ChangeSet(modified=[ChunkRef("c.py", "c.py::send", "f9", "t1")])
    r = reflect(ws, cs, Graph(), propose=lambda *a, **k: [])
    assert r.caused == {"h1": ho.id}
    assert any(o.caused_by == ho.id for o in ws.store.ops if o.kind == K.BIND_REFRESH)
    done = settle(ws, r.caused, sha="abc123")
    assert len(done) == 1 and done[0].caused_by == ho.id
    assert phase(ws.store, "h1") == "synced"
    assert queued(ws.store) == [] and write_queue(tmp_path / ".codoc", ws.store) is None


def test_withdraw_restores_the_previous_frontier(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " More."}}], actor="human:local")
    handoff(ws, "h1", actor="human:local")
    assert phase(ws.store, "h1") == "queued"
    assert withdraw(ws, "h1", actor="human:local") is not None
    assert phase(ws.store, "h1") == "edited"


def test_deleted_bound_feature_stays_queued_as_phantom(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor="human:local")
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"},
                "payload": {"covers": dict(ws.store.frontier), "delete_code": True}}], actor="human:local")
    assert queued(ws.store) == ["h1"]
    md = render_queue(ws.store)
    assert "REMOVE FEATURE" in md and "Delete the code" in md
