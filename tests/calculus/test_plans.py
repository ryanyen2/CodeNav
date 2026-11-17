"""Plan verdicts: accept, reject and reopen act on a plan's own ops by identity."""
from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.derive import phase
from codoc.calculus.doc import PARAGRAPH
from codoc.calculus.export import export_markdown
from codoc.calculus.ids import START
from codoc.calculus.store import Store
from codoc.index.changes import ChangeSet
from codoc.index.graph import Graph
from codoc.loops.reflect import reflect
from codoc.workspace import Workspace
from tests.helpers import (
    H,
    Mint,
    T,
    chunk,
    heading,
    ins,
    refs,
    seed,
    seed_loop,
    ws_paragraph,
)


def test_subset_accept_replays_as_the_same_subset(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ins = ws_paragraph(ws, "p1", "base")
    pid = ws.open_plan(actor="agent:x")
    i1 = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": [ins.id, 3], "text": " A"}}], actor="agent:x", plan=pid)[0]
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": [i1.id, 1], "text": " B"}}], actor="agent:x", plan=pid)
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {"subset": [i1.id]}}], actor="human:local")[0]
    assert T(ws.store, "p1") == "base A" and acc.payload["subset"] == [i1.id]
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    assert T(ws2.store, "p1") == "base A"
    assert ws2.store.state_fingerprint() == ws.store.state_fingerprint()


def test_export_leaves_open_plans_out(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="h1")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Maybe."}},
               {"kind": K.BLOCK_ADD, "target": {"bid": "p9"}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p9", "path": "text"}, "payload": {"after": START, "text": "Whole new paragraph."}}],
              actor="loop", plan=pid)
    md = export_markdown(ws.store)
    assert "Maybe" not in md and "Whole new" not in md and "Retries once before giving up." in md


def test_accepting_a_retire_plan_detaches_and_reopen_restores(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    retire = lambda c, s, t, **kw: [{"kind": "retire_node", "feature_id": "h1", "rationale": "gone"}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f2", "t1")]), Graph(), propose=retire)
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": r.plans[0]}, "payload": {}}], actor="human:local")[0]
    assert not ws.store.blocks["h1"].live and ("c.py", "c.py::send") not in ws.store.bindings
    ws.submit([dict(b) for b in acc.inverse], actor="human:local")
    assert ws.store.blocks["h1"].live and ws.store.bindings[("c.py", "c.py::send")].feature == "h1"


def test_plan_reject_restores_a_binding_the_plan_only_refreshed():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h1"}, {"file": "c.py", "symbol": "C", "fingerprint": "f1"}))
    apply(s, m.op(K.PLAN_OPEN, {"pid": "p-1"}, {"kind": "edit", "target": "h1"}, actor="agent:x"))
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h1"}, {"file": "c.py", "symbol": "C", "fingerprint": "f2"}, actor="agent:x", plan="p-1"))
    apply(s, m.op(K.PLAN_REJECT, {"pid": "p-1"}, {}))
    assert s.bindings[("c.py", "C")].fingerprint == "f1"


def test_a_heading_deleted_under_a_paragraph_plan_and_restored_keeps_the_plan_decidable(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws, actor="loop")
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    ins(ws, "p1", list(refs(ws, "p1")[-1]), " Proposed.", actor="loop", plan=pid)
    from codoc.calculus.derive import open_plans, projection
    assert [p.id for p in open_plans(ws.store)] == [pid]
    d = ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor=H)[0]
    assert open_plans(ws.store) == []                      # not offered while its words are dead
    projection(ws.store)                                   # renders without error
    ws.submit([dict(b) for b in d.inverse], actor=H)
    assert [p.id for p in open_plans(ws.store)] == [pid]   # back with the heading
    assert phase(ws.store, "h1") == "proposed"
    ws.close()
