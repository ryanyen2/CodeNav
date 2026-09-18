"""The agent's own write surface (`codoc/mcp/tools.py`).

An agent reaches the tree through the MCP tools and nothing else, so what the
tools promise — "small repairs land, rewrites and repairs on a feature with
pending intent become a plan" — has to be what the tree does.
"""
from codoc.calculus import ops as K
from codoc.calculus.derive import markdown, phase
from codoc.calculus.doc import PARAGRAPH
from codoc.calculus.ids import START
from codoc.calculus.text import BOLD
from codoc.loops.realize import handoff
from codoc.mcp import tools
from codoc.substrate import client
from codoc.substrate.publish import publish
from codoc.substrate.workspace import Workspace
from tests.substrate.test_review5 import H, refs, seed


def workspace(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    publish(cd, ws.store, root_dir=str(tmp_path))
    return cd, ws


def test_an_agents_rewrite_of_a_queued_feature_is_a_plan(tmp_path):
    """01 §4 rule 8: while a feature is queued the document wins, whatever the
    size of the repair. The gate used to live only in the loop's own pass, so
    the same words arriving through the tool landed on the trunk."""
    cd, ws = workspace(tmp_path)
    handoff(ws, "h1", actor=H, asked="retry twice")
    assert phase(ws.store, "h1") == "queued"
    ws.close()
    r = tools.reflect(str(cd), ops=[{"kind": "amend", "feature_id": "h1",
                                     "blocks": [{"id": "p1", "text": "Completely different words, nothing kept."}]}],
                      root_dir=str(tmp_path))
    client.release(cd)
    assert r["ok"], r
    st = Workspace(cd, readonly=True).store
    trunk = [s for s in st.blocks["p1"].text().segs if s.live and s.plan is None and s.actor.startswith("agent:")]
    assert not trunk, [s.to_json() for s in st.blocks["p1"].text().segs]
    assert markdown(st.blocks["p1"].text(), "rejected", st) == "Retries once."
    assert [p.actor for p in st.plans.values() if p.status == "open"] == ["agent:claude-code"]


def test_an_agents_amend_does_not_drop_a_mark_the_person_placed(tmp_path):
    """01 §4 rule 5. The refusal covered the loop and a translator; an agent
    rewriting the same sentence took the bold with it."""
    cd, ws = workspace(tmp_path)
    r = refs(ws, "p1")
    ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": BOLD, "attrs": {}, "start": list(r[0]), "end": list(r[6]), "on": True}}], actor=H)
    ws.close()
    tools.reflect(str(cd), ops=[{"kind": "amend", "feature_id": "h1",
                                 "blocks": [{"id": "p1", "text": "Retries twice."}]}], root_dir=str(tmp_path))
    client.release(cd)
    st = Workspace(cd, readonly=True).store
    assert markdown(st.blocks["p1"].text(), "rejected", st) == "**Retries** once."


def test_a_small_repair_to_the_machines_own_prose_still_lands(tmp_path):
    """The gate is a gate, not a wall: the tool's contract is that small
    repairs land."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws, actor="loop")
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    tools.reflect(str(cd), ops=[{"kind": "amend", "feature_id": "h1",
                                 "blocks": [{"id": "p1", "text": "Retries once more."}]}], root_dir=str(tmp_path))
    client.release(cd)
    st = Workspace(cd, readonly=True).store
    assert st.blocks["p1"].text().live_text() == "Retries once more."
    assert not [p for p in st.plans.values() if p.status == "open"]


def test_one_amend_over_two_paragraphs_is_one_proposal(tmp_path):
    """01 §4 rule 8: "One amend is one plan: every block it repairs ... rides
    under the same plan id, so one repair gets one verdict." The tool sent one
    ingest per block with no batch, and `_plan_for` fell back to keying on the
    block id — a proposal per paragraph for the person to answer."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": START, "text": "Backs off."}}], actor=H)
    handoff(ws, "h1", actor=H)                            # queued: the document wins, so both repairs propose
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    try:
        out = tools.reflect(str(cd), ops=[{"kind": "amend", "feature_id": "h1", "blocks": [
            {"id": "p1", "text": "Retries twice."}, {"id": "p2", "text": "Backs off exponentially."}]}],
            root_dir=str(tmp_path))
        assert out["ok"], out
        st = client.read(cd).store
        open_plans = [p for p in st.plans.values() if p.status == "open" and p.actor == "agent:claude-code"]
        assert len(open_plans) == 1, [(p.id, p.target, p.ops) for p in open_plans]
        assert len(open_plans[0].ops) > 1                  # and both repairs are under it
    finally:
        client.release(cd)


def test_a_repair_never_rides_in_the_agents_open_proposal_to_delete(tmp_path):
    """`_plan_for` reused "an open plan of theirs on the same block" whatever
    its kind, so an agent that proposed retiring a feature and then repaired
    its title had the repair filed under the DELETE: accepting the deletion
    accepted the repair, rejecting it dropped it."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    handoff(ws, "h1", actor=H)                            # queued, so the repair must propose
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    try:
        assert tools.propose(str(cd), ops=[{"kind": "retire_node", "feature_id": "h1"}], note="dead code",
                             root_dir=str(tmp_path))["ok"]
        tools.reflect(str(cd), ops=[{"kind": "amend", "feature_id": "h1", "title": "Server client"}],
                      root_dir=str(tmp_path))
        st = client.read(cd).store
        riders = {st.plans[o.plan].kind for o in st.ops
                  if o.kind == K.TEXT_INSERT and o.plan and "Server" in o.payload.get("text", "")}
        assert riders and "delete" not in riders, riders
    finally:
        client.release(cd)
