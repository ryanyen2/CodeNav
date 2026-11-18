"""The agent's own write surface (`codoc/mcp/tools.py`).

An agent reaches the tree through the MCP tools and nothing else, so what the
tools promise — "small repairs land, rewrites and repairs on a feature with
pending intent become a plan" — has to be what the tree does.
"""
from codoc.calculus import ops as K
from codoc.calculus.derive import accept_handoffs, markdown, phase
from codoc.calculus.doc import PARAGRAPH
from codoc.calculus.ids import START
from codoc.calculus.text import BOLD
from codoc.daemon import client
from codoc.daemon.publish import publish
from codoc.mcp import tools
from codoc.workspace import Workspace
from tests.helpers import H, T, handoff, refs, seed


def workspace(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    publish(cd, ws.store, root_dir=str(tmp_path))
    return cd, ws


def test_an_agents_rewrite_of_a_queued_feature_is_a_plan(tmp_path):
    """While a feature is queued the document wins, whatever the size of the
    repair: an agent's rewrite through the tool is a plan, as the loop's is."""
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
    """A rewriter may not drop a mark a person placed. An agent's amend
    through the tool is held to it, as the loop and a translator are."""
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
    """One amend is one plan: every block it repairs rides under the same
    plan id, so one repair gets one verdict, not one per paragraph."""
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
    """The applier reuses an open `edit` plan of the actor on the same block,
    never a `delete` plan. A repair an agent makes after proposing to retire a
    feature is therefore its own proposal, and the verdict on the deletion does
    not decide it."""
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


def test_the_tools_do_not_offer_a_proposal_nobody_can_answer(tmp_path):
    """A plan whose target block is dead is not offered (`derive.open_plans`),
    and `read_tree` and `read_status` count the plans the editor draws, so the
    agent never reads a proposal the person cannot answer."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    pid = ws.open_plan(actor="agent:claude-code", kind="edit", target="p1")
    r = refs(ws, "p1")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(r[-1]), "text": " Proposed."}}], actor="agent:claude-code", plan=pid)
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    assert tools.read_status(str(cd))["proposals"] == 1
    assert [p["plan_id"] for p in tools.read_tree(str(cd))["plans"]] == [pid]
    client.release(cd)
    ws = Workspace(cd)
    d = ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor=H)[0]
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    assert tools.read_status(str(cd))["proposals"] == 0
    assert tools.read_tree(str(cd))["plans"] == []
    client.release(cd)
    ws = Workspace(cd)                                   # …and it comes back with the block
    ws.submit([dict(b) for b in d.inverse], actor=H)
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    assert tools.read_status(str(cd))["proposals"] == 1
    client.release(cd)


def test_accepting_a_proposal_builds_but_accepting_a_reflection_does_not(tmp_path):
    """A `codoc_propose` plan comes before the code, so accepting it hands off
    the feature it adds. A `codoc_reflect` plan describes code the agent
    already wrote, so accepting it takes the words and queues nothing."""
    cd, ws = workspace(tmp_path)
    ws.close()
    try:
        refl = tools.reflect(str(cd), ops=[{"kind": "add_node", "title": "Backoff", "description": "Waits."}],
                             root_dir=str(tmp_path))
        prop = tools.propose(str(cd), ops=[{"kind": "add_node", "title": "Jitter", "description": "Spreads."}],
                             root_dir=str(tmp_path))
        st = client.read(cd).store
        assert accept_handoffs(st, st.plans[refl["planned"]["plan_id"]]) == []
        jitter = next(h.id for h in st.headings() if h.text().live_text() == "Jitter")
        assert accept_handoffs(st, st.plans[prop["plan_id"]]) == [jitter]
    finally:
        client.release(cd)


def test_a_proposal_that_writes_nothing_says_so_and_leaves_no_plan(tmp_path):
    """A second rewording of a paragraph that holds the first proposal's words
    is refused. The call reports it, and the empty plan is taken back, so the
    person is not asked to accept (and build) nothing."""
    cd, ws = workspace(tmp_path)
    ws.close()
    try:
        def reword(text: str) -> dict:
            return tools.propose(str(cd), ops=[{"kind": "amend", "feature_id": "h1",
                                                "blocks": [{"id": "p1", "text": text}]}], root_dir=str(tmp_path))

        unchanged = reword(T(ws.store, "p1"))
        assert not unchanged["ok"] and "no block would change" in unchanged["error"]
        assert reword("Retries twice.")["ok"]
        second = reword("Retries three times.")
        assert not second["ok"] and "open_plan" in second["error"]
        st = client.read(cd).store
        assert [p for p in st.open_plans.values() if not p.ops] == []
    finally:
        client.release(cd)


def test_a_refused_part_does_not_hide_the_plans_that_were_written(tmp_path):
    """A refused rewording beside a removal: the call is ok, names the delete
    plan it opened (a delete plan needs no struck words), and reports the refusal."""
    cd, ws = workspace(tmp_path)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": "heading", "level": 1, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "Old"}}],
              actor=H)
    publish(cd, ws.store, root_dir=str(tmp_path))
    ws.close()
    try:
        reword = {"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": "Retries twice."}]}
        assert tools.propose(str(cd), ops=[reword], root_dir=str(tmp_path))["ok"]
        got = tools.propose(str(cd), ops=[reword, {"kind": "retire_node", "feature_id": "h2"}], root_dir=str(tmp_path))
        st = client.read(cd).store
        assert got["ok"] and [st.plans[p].kind for p in got["plan_ids"]] == ["delete"]
        assert any("open_plan" in r for r in got["refused"])
    finally:
        client.release(cd)


def test_a_feature_is_proposed_for_removal_once(tmp_path):
    """Two `retire_node` of one feature in one call open one delete plan, and
    a later call adds none while that plan is open."""
    cd, ws = workspace(tmp_path)
    ws.close()
    try:
        retire = {"kind": "retire_node", "feature_id": "h1"}
        assert tools.propose(str(cd), ops=[retire, retire], root_dir=str(tmp_path))["ok"]
        assert not tools.propose(str(cd), ops=[retire], root_dir=str(tmp_path))["ok"]
        st = client.read(cd).store
        assert [p.target for p in st.plans.values() if p.kind == "delete" and p.status == "open"] == ["h1"]
    finally:
        client.release(cd)


def test_codoc_agent_names_the_actor_and_its_citing_attach_settles_the_handoff(tmp_path, monkeypatch):
    """Cursor or Codex run this MCP server with CODOC_AGENT set and run no
    Claude Code hooks, so no agent epoch ever closes. Their attach that cites
    the hand-off is the evidence, and the applier settles on it, here with no
    daemon running."""
    from codoc.loops.realize import queued
    cd, ws = workspace(tmp_path)
    ho = handoff(ws, "h1", actor=H)
    ws.close()
    monkeypatch.setenv("CODOC_AGENT", "cursor")
    try:
        r = tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": "h1", "bindings": ["c.py::retry"]}],
                          caused_by=ho.id, root_dir=str(tmp_path))
    finally:
        client.release(cd)
    assert r["ok"], r
    st = Workspace(cd, readonly=True).store
    assert st.by_id[r["applied"][0]["op"]].actor == "agent:cursor"
    assert queued(st) == [] and phase(st, "h1") == "synced"
    assert [o.caused_by for o in st.ops if o.kind == K.FEATURE_REALIZED] == [ho.id]


def test_the_queues_d_id_settles_the_handoff(tmp_path):
    """realize.md shows a hand-off as ⟨d:<op id>⟩ and the agent is told to pass
    that id as caused_by; it must cite the hand-off the same as the bare id."""
    from codoc.loops.realize import queued
    cd, ws = workspace(tmp_path)
    ho = handoff(ws, "h1", actor=H)
    ws.close()
    try:
        r = tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": "h1", "bindings": ["c.py::retry"]}],
                          caused_by=f"d:{ho.id}", root_dir=str(tmp_path))
    finally:
        client.release(cd)
    assert r["ok"], r
    assert queued(Workspace(cd, readonly=True).store) == []


def test_only_a_call_whose_ops_landed_cites_its_handoff(tmp_path):
    """The Stop hook asks about each queued item no call of the turn cited. A
    call that errored reported nothing, so it cites nothing."""
    from codoc.daemon.epoch import read_activity, write_activity
    cd, ws = workspace(tmp_path)
    ho = handoff(ws, "h1", actor=H)
    ws.close()
    write_activity(str(cd), {"epoch": {"id": "ep", "open": True}, "cited": []})
    cite, root = f"d:{ho.id}", str(tmp_path)
    try:
        assert not tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": "nope", "bindings": ["c.py::x"]}],
                                 caused_by=cite, root_dir=root)["ok"]
        assert not tools.propose(str(cd), ops=[], caused_by=cite, root_dir=root)["ok"]
        assert not tools.write_block(str(cd), content={}, kind="nope", feature_id="h1", caused_by=cite, root_dir=root)["ok"]
        assert read_activity(str(cd))["cited"] == []
        assert tools.reflect(str(cd), ops=[{"kind": "attach", "feature_id": "h1", "bindings": ["c.py::retry"]}],
                             caused_by=cite, root_dir=root)["ok"]
    finally:
        client.release(cd)
    assert read_activity(str(cd))["cited"] == [ho.id]


def test_the_agent_is_claude_code_unless_codoc_agent_names_another(monkeypatch):
    from codoc.workspace import log as L
    monkeypatch.delenv("CODOC_AGENT", raising=False)
    assert L.actor_for(tools.agent_client()) == "agent:claude-code"
    monkeypatch.setenv("CODOC_AGENT", "codex")
    assert L.actor_for(tools.agent_client()) == "agent:codex"
    # The name becomes an inbox file name and an actor id: anything else refuses to start.
    import pytest
    from codoc.mcp import server
    monkeypatch.setenv("CODOC_AGENT", "../Cursor")
    with pytest.raises(SystemExit, match="CODOC_AGENT"):
        server.main()
    assert L.actor_for("agent-../Cursor") is None
