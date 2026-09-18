"""Review-3 probes, second batch (workflows). Each asserts the CORRECT behaviour."""
import json

from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.derive import history, markdown, phase
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.export import export_markdown, import_markdown
from codoc.calculus.ids import START
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD
from codoc.graph.graph import Graph
from codoc.loop.diff import ChangeSet
from codoc.loops.reflect import reflect
from codoc.substrate.workspace import Workspace
from tests.calculus.test_apply import Mint, heading, paragraph
from tests.calculus.test_derive_export import build
from tests.loops.test_reflect import chunk, seed


def T(s, bid):
    return s.blocks[bid].text().live_text()


def test_P17_translate_tombstones_an_open_plans_words(tmp_path):
    from codoc.doclang import resolve
    from codoc.loops.translate import translate_tree
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="h1")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " Maybe."}}], actor="loop", plan=pid)
    propose = lambda payload, lang, **kw: {p["id"]: ("客户端", "放弃前重试一次。") for p in payload}
    res = translate_tree(ws, tmp_path / ".codoc", language=resolve("zh-Hans"), propose=propose)
    assert res.translated == 1, res.summary()
    plan_words = [s for s in ws.store.blocks["p1"].text().segs if s.plan == pid]
    assert plan_words and all(s.live for s in plan_words), [(s.chars, s.dead) for s in plan_words]
    assert ws.store.plans[pid].status == "open"


def test_P20_accepting_a_retire_plan_leaves_bindings_on_the_dead_heading(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    retire = lambda c, s, t, **kw: [{"kind": "retire_node", "feature_id": "h1", "rationale": "gone"}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f2", "t1")]), Graph(), propose=retire)
    assert len(r.plans) == 1
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": r.plans[0]}, "payload": {}}], actor="human:local")
    assert not ws.store.blocks["h1"].live
    assert ("c.py", "c.py::send") not in ws.store.bindings          # spec §3: a deleted heading's bindings are detached


def test_P20b_a_persons_delete_of_a_bound_heading_leaves_bindings_behind(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor="human:local")
    assert ("c.py", "c.py::send") not in ws.store.bindings
    # and the next pass over that code is told a dead owner
    cs = ChangeSet(modified=[chunk("c.py", "c.py::send", "f3", "t1")])
    seen = {}
    def spy(changes, subtree, titles, **kw):
        seen.update(changes); return []
    reflect(ws, cs, Graph(), propose=spy)
    assert all(ws.store.blocks[m["current_feature_id"]].live for m in seen.get("modified", []))


def test_P21_undo_of_an_accept_after_later_typing_then_reject(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="h1")
    t = ws.store.blocks["p1"].text()
    pi = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                     "payload": {"after": list(t.last_live_ref()), "text": " Proposed words."}}], actor="loop", plan=pid)[0]
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:local")[0]
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": [pi.id, 9], "text": " (mine)"}}], actor="human:local")        # types inside the accepted words
    ws.submit([dict(b) for b in acc.inverse], actor="human:local")                                  # undo the accept
    assert ws.store.plans[pid].status == "open"
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor="human:local")
    assert T(ws.store, "p1") == "Retries once before giving up. (mine)"                              # honest: the person's words survive


def test_P22_export_skips_a_plan_heading_but_keeps_a_trunk_paragraph_under_it():
    m, s = Mint(), Store()
    heading(m, s, "b-000000000001", "Top")
    apply(s, m.op(K.PLAN_OPEN, {"pid": "p-1"}, {"kind": "add", "target": "b-000000000002"}, actor="loop"))
    heading(m, s, "b-000000000002", "Proposed", after="b-000000000001", plan="p-1", actor="loop")
    paragraph(m, s, "b-000000000003", "A person wrote this under the proposal.", after="b-000000000002", actor="human:a")
    md = export_markdown(s)
    s2 = Store()
    build(s2, Mint("t"), import_markdown(md))
    # export(rejected) cannot represent this store; import puts the paragraph under Top.
    assert export_markdown(s2) == md
    assert [b.kind for b in s2.ordered()] == ["heading", "paragraph"]


def test_P23_two_plans_on_one_block_accept_one_reject_other(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    a = ws.open_plan(actor="loop", kind="edit", target="h1")
    b = ws.open_plan(actor="agent:x", kind="edit", target="h1")
    t = ws.store.blocks["p1"].text()
    ia = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                     "payload": {"after": list(t.last_live_ref()), "text": " A."}}], actor="loop", plan=a)[0]
    ib = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                     "payload": {"after": [ia.id, 2], "text": " B."}}], actor="agent:x", plan=b)[0]
    assert ib.conflict is None
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": b}, "payload": {}}], actor="human:local")
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": a}, "payload": {}}], actor="human:local")
    assert T(ws.store, "p1") == "Retries once before giving up. B."
    assert markdown(ws.store.blocks["p1"].text(), "rejected", ws.store) == "Retries once before giving up. B."


def test_P24_a_refused_verdict_is_in_no_history(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="h1")
    bad = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="agent:x")[0]
    assert bad.conflict == "authority"
    assert bad in history(ws.store, "h1")


def test_P25_machine_may_ride_a_strangers_plan(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    pid = ws.open_plan(actor="human:contrib", kind="edit", target="h1")
    t = ws.store.blocks["p1"].text()
    op = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                     "payload": {"after": list(t.last_live_ref()), "text": " Agent words."}}], actor="agent:claude-code", plan=pid)[0]
    assert op.conflict == "authority"
