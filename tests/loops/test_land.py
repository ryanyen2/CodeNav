"""Where a machine's tree edits land: Workspace.amend, reflect's _land, the MCP writes."""
from codoc.calculus import ops as K
from codoc.calculus.derive import block_written_by
from codoc.index.changes import ChangeSet
from codoc.index.graph import Graph
from codoc.loops.edits import Compiler
from codoc.loops.realize import handoff
from codoc.loops.reflect import ReflectResult, _land, _Pass, reflect
from codoc.workspace import Workspace
from tests.helpers import H, T, chunk, seed, seed_loop


def test_a_machine_rewrite_never_deletes_an_open_plans_words(tmp_path):
    """The model is shown the description without an open plan's words. Its
    rewrite of that block is refused, so the proposal is not deleted on the trunk."""
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)                                       # "Retries once before giving up."
    pid = ws.open_plan(actor="agent:claude-code", kind="edit", target="p1")
    anchor = list(ws.store.blocks["p1"].text().ref_at(len("Retries once") - 1))
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": anchor, "text": " with backoff"}}], actor="agent:claude-code", plan=pid)
    res = ReflectResult()
    _land(_Pass(ws, res, {}), Compiler(ws.store).compile(
        {"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": "Retries twice before giving up."}]}))
    assert res.refused == [("p1", "open_plan")]
    assert T(ws.store, "p1") == "Retries once with backoff before giving up."
    assert ws.store.plans[pid].status == "open"
    ws.close()


def test_a_detach_from_the_model_lands_on_the_trunk(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    res = ReflectResult()
    assert _land(_Pass(ws, res, {}), Compiler(ws.store).compile(
        {"kind": "detach", "feature_id": "h1", "bindings": ["c.py::send"]}))
    assert ("c.py", "c.py::send") not in ws.store.bindings and res.auto["detach"] == 1
    ws.close()


def test_one_machine_clause_does_not_loosen_the_gate_on_a_persons_paragraph(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    base = "Sends each request over a pooled connection and retries twice on timeout."
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"},
                "payload": {"from": list(t.ref_at(0)), "to": list(t.last_live_ref())}}], actor=H)
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": "START", "text": base}}], actor=H)
    handoff(ws, "h1", actor=H)
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {}}], actor="loop")
    ws.ingest("p1", "text", base_text=base, new_md=base[:-1] + ", never on 4xx.", actor="loop")
    assert block_written_by(ws.store, "p1") == "human"
    cur = T(ws.store, "p1")
    res, ops = ws.ingest("p1", "text", base_text=cur,
                         new_md="Sends each request and retries twice on timeout, never on 4xx.", actor="loop")
    assert 0.15 < res.deleted_fraction < 0.5
    assert ops and all(o.plan for o in ops if o.kind.startswith("text."))      # a proposal, not a trunk rewrite
    assert "over a pooled connection" in T(ws.store, "p1")
    ws.close()


def test_one_pass_proposes_a_feature_for_deletion_once(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    cs = ChangeSet(removed=[chunk("c.py", "c.py::send", "f1", "t1")], added=[chunk("d.py", "d.py::x", "fx", "tx")])
    model = lambda changes, subtree, titles, **k: [
        {"kind": "retire_node", "feature_id": "h1", "rationale": "send was removed"},
        {"kind": "add_node", "title": "Thing x", "description": "Does x.", "bindings": ["d.py::x"]}]
    reflect(ws, cs, Graph(), propose=model)
    deletes = [p for p in ws.store.plans.values() if p.status == "open" and p.kind == "delete"]
    assert [p.target for p in deletes] == ["h1"]
    ws.close()


def test_an_amend_is_one_decision_on_both_paths(tmp_path):
    """A small rewording and a large rewrite of a person's block in one amend:
    the loop and an agent through MCP both make one plan for all of it."""
    from codoc.calculus.ids import START
    from codoc.daemon import client
    from codoc.mcp import tools

    def seeded(cd):
        ws = Workspace(cd)
        seed(ws)                                        # p1 "Retries once." by a person
        ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": "paragraph", "after": "p1"}},
                   {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"},
                    "payload": {"after": START, "text": "Then it gives up for good."}}], actor="loop")
        return ws

    edit = {"kind": "amend", "feature_id": "h1", "blocks": [
        {"id": "p2", "text": "Then it gives up."}, {"id": "p1", "text": "Completely different words now."}]}
    ws = seeded(tmp_path / "loop" / ".codoc")
    res = ReflectResult()
    _land(_Pass(ws, res, {}), Compiler(ws.store).compile(edit))
    text_ops = [o for o in res.ops if o.kind.startswith("text.")]
    assert len(res.plans) == 1 and all(o.plan == res.plans[0] for o in text_ops)
    ws.close()

    cd = tmp_path / "mcp" / ".codoc"
    seeded(cd).close()
    try:
        out = tools.reflect(str(cd), ops=[edit], root_dir=str(tmp_path / "mcp"))
        assert out["ok"] and out["amended"][0]["status"].startswith("plan:")
        st = client.read(cd).store
        plans = {o.plan for o in st.ops if o.kind.startswith("text.") and o.actor.startswith("agent:")}
        assert len(plans) == 1 and None not in plans
    finally:
        client.release(cd)


def test_propose_makes_each_removal_its_own_delete_plan(tmp_path):
    from codoc.daemon import client
    from codoc.mcp import tools
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    ws.close()
    try:
        out = tools.propose(str(cd), ops=[{"kind": "retire_node", "feature_id": "h1"},
                                          {"kind": "add_node", "title": "Replacement", "description": "Does it."}],
                            root_dir=str(tmp_path))
        assert out["ok"] and len(out["plan_ids"]) == 2
        st = client.read(cd).store
        kinds = sorted((st.plans[p].kind, st.plans[p].target) for p in out["plan_ids"])
        assert kinds[1] == ("delete", "h1") and kinds[0][0] == "add"
        client.write(cd, client="cli", bodies=[{"kind": K.PLAN_ACCEPT, "target": {"pid": p}, "payload": {}}
                                               for p in out["plan_ids"]])
        st = client.read(cd).store
        assert not st.blocks["h1"].live and ("c.py", "c.py::send") not in st.bindings
    finally:
        client.release(cd)
