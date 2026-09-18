"""The reflect loop on the calculus, with an injected LLM."""
from codoc.calculus import ops as K
from codoc.calculus.derive import description, phase, plan_claims, title
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.ids import START
from codoc.graph.graph import Graph
from codoc.loop.diff import ChangeSet, ChunkRef
from codoc.loops.reflect import reflect
from codoc.substrate.workspace import Workspace


def seed(ws: Workspace):
    ws.submit([
        {"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": HEADING, "level": 1, "after": START}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "h1", "path": "text"}, "payload": {"after": START, "text": "Client"}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": "h1"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "Retries once before giving up."}},
        {"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": "c.py::send", "fingerprint": "f1", "types_hash": "t1"}},
    ], actor="loop")


def chunk(file, sym, fp="", th="", src=""):
    return ChunkRef(file=file, symbol_path=sym, fingerprint=fp, types_hash=th, source=src)


def test_deterministic_maintenance_needs_no_llm(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    calls = []
    cs = ChangeSet(modified=[chunk("c.py", "c.py::send", "f2", "t1")])
    r = reflect(ws, cs, Graph(), propose=lambda *a, **k: calls.append(a) or [])
    assert r.auto["refresh"] == 1 and r.llm_calls == 1     # a modified bound chunk asks whether prose is stale
    assert ws.store.bindings[("c.py", "c.py::send")].fingerprint == "f2"
    # A relocation (same content, new name) rebinds without the model.
    cs = ChangeSet(removed=[chunk("c.py", "c.py::send", "f2", "t1")], added=[chunk("c.py", "c.py::deliver", "f2", "t1")])
    r = reflect(ws, cs, Graph(), propose=lambda *a, **k: [])
    assert r.auto["detach"] == 1 and r.auto["relocate"] == 1
    assert ws.store.bindings[("c.py", "c.py::deliver")].feature == "h1"
    assert r.llm_calls == 0


def test_llm_amend_is_block_keyed_and_small_repairs_land_on_trunk(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)

    def propose(changes, subtree, titles, **kw):
        me = next(e for e in subtree if e["id"] == "h1")
        assert me["blocks"][0]["id"] == "p1"
        return [{"kind": "amend", "feature_id": "h1",
                 "blocks": [{"id": "p1", "text": "Retries three times before giving up."}],
                 "rationale": "the retry count changed"}]

    cs = ChangeSet(modified=[chunk("c.py", "c.py::send", "f3", "t1")])
    r = reflect(ws, cs, Graph(), propose=propose)
    assert not r.plans and not r.refused
    assert description(ws.store, "h1") == "Retries three times before giving up."
    assert phase(ws.store, "h1") == "synced"


def test_large_rewrite_and_held_feature_become_plans(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    big = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": "Entirely new prose about something else."}]}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f3", "t1")]), Graph(), propose=big)
    assert len(r.plans) == 1
    assert [c.stage for c in plan_claims(ws.store, "h1")] and phase(ws.store, "h1") == "proposed"
    assert "Retries once" in description(ws.store, "h1")          # live shows both; the plan is gray
    # Reject it; the human then types (unsent ink) and a small repair must become a plan too.
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": r.plans[0]}, "payload": {}}], actor="human:local")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Fix this."}}],
              actor="human:local")
    small = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": ws.store.blocks["p1"].text().live_text().replace("once", "twice")}]}]
    r2 = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f4", "t1")]), Graph(), propose=small)
    assert len(r2.plans) == 1 and ws.store.plans[r2.plans[0]].note == "the document has pending intent"


def test_add_node_is_a_plan_with_its_bindings_and_coverage_net(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    added = [chunk("s.py", "s.py::save", "f9", "t9", "def save(): ..."), chunk("s.py", "s.py::load", "f8", "t8")]

    def propose(changes, subtree, titles, **kw):
        assert [a["symbol_path"] for a in changes["added"]] == ["s.py::save", "s.py::load"]
        return [{"kind": "add_node", "parent_id": "h1", "title": "Storage", "description": "Saves and loads state.",
                 "bindings": [["s.py", "s.py::save"]], "rationale": "no node covered persistence"}]

    r = reflect(ws, ChangeSet(added=added, rows=[]), Graph(), propose=propose)
    assert len(r.plans) == 2                      # the add plan, plus a coverage plan for the unplaced chunk
    plan = ws.store.plans[r.plans[0]]
    assert plan.kind == "add" and ws.store.blocks[plan.target].level == 2
    assert title(ws.store, plan.target) == "Storage"
    assert ws.store.bindings[("s.py", "s.py::save")].feature == plan.target
    assert any(ws.store.by_id[i].kind == K.BIND_ATTACH for i in plan.ops)   # the binding is part of the proposal
    # Accept: the node's words keep the loop as author and it is now a real feature.
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": plan.id}, "payload": {}}], actor="human:local")
    assert phase(ws.store, plan.target) == "synced"
    assert description(ws.store, plan.target) == "Saves and loads state."


def test_emptied_feature_becomes_delete_plan_and_accept_deletes(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    r = reflect(ws, ChangeSet(removed=[chunk("c.py", "c.py::send", "f1", "t1")]), Graph(), propose=lambda *a, **k: [])
    assert r.auto["detach"] == 1 and len(r.plans) == 1
    p = ws.store.plans[r.plans[0]]
    assert p.kind == "delete" and p.target == "h1"
    assert [c.stage for c in plan_claims(ws.store, "h1")] == ["removed", "removed"]
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": p.id}, "payload": {}}], actor="human:local")
    assert not ws.store.blocks["h1"].live and not ws.store.blocks["p1"].live
