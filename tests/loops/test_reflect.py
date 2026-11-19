"""The reflect loop on the calculus, with an injected LLM."""
from codoc.calculus import ops as K
from codoc.calculus.derive import description, markdown, phase, plan_claims, title
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.derive import export_markdown
from codoc.calculus.ids import START
from codoc.calculus.text import BOLD
from codoc.index.changes import ChangeSet
from codoc.index.graph import Graph
from codoc.loops.reflect import reflect
from codoc.workspace import Workspace
from tests.helpers import chunk, H, handoff, md_of, seed, seed_loop, T


def test_a_malformed_attach_does_not_make_a_feature_one_that_lost_its_code(tmp_path):
    """`reflect._emptied` proposes deleting a feature that had code and has
    none now. A `bind.attach` that conflicts adds nothing to `bound_once`, so
    a malformed attach does not make a feature look like one that lost its
    code."""
    from codoc.loops.reflect import _emptied
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([
        {"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": HEADING, "level": 1, "after": START}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "h1", "path": "text"}, "payload": {"after": START, "text": "Client"}},
    ], actor="loop")
    bad = ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {}}], actor="loop")[0]
    assert bad.conflict == "missing:symbol"
    assert _emptied(ws.store) == [], ("never had code, proposed for deletion:", ws.store.bound_once)


def test_deterministic_maintenance_needs_no_llm(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    calls = []
    cs = ChangeSet(modified=[chunk("c.py::send", "f2", "t1")])
    r = reflect(ws, cs, Graph(), propose=lambda *a, **k: calls.append(a) or [])
    assert r.auto["refresh"] == 1 and r.llm_calls == 1     # a modified bound chunk asks whether prose is stale
    assert ws.store.bindings["c.py::send"].fingerprint == "f2"
    # A relocation (same content, new name) rebinds without the model.
    cs = ChangeSet(removed=[chunk("c.py::send", "f2", "t1")], added=[chunk("c.py::deliver", "f2", "t1")])
    r = reflect(ws, cs, Graph(), propose=lambda *a, **k: [])
    assert r.auto["detach"] == 1 and r.auto["relocate"] == 1
    assert ws.store.bindings["c.py::deliver"].feature == "h1"
    assert r.llm_calls == 0


def test_a_binding_is_keyed_by_its_symbol_alone(tmp_path):
    """The bind op names only the symbol. The index's chunk finds the binding
    by its symbol path, and a cite in the prose names the same key."""
    from codoc.calculus.inline_md import parse
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    attach = next(o for o in ws.store.ops if o.kind == K.BIND_ATTACH)
    assert "file" not in attach.payload and attach.payload["symbol"] == "c.py::send"
    reflect(ws, ChangeSet(modified=[chunk("c.py::send", "f2", "t1")]), Graph(), propose=lambda *a, **k: [])
    b = ws.store.bindings["c.py::send"]
    assert (b.feature, b.file, b.fingerprint) == ("h1", "c.py", "f2")
    _, spans = parse("[send](codoc:c.py::send)")
    assert spans[0].attrs == {"symbol": "c.py::send"} and ws.store.bindings[spans[0].attrs["symbol"]] is b


def test_llm_amend_is_block_keyed_and_small_repairs_land_on_trunk(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)

    def propose(changes, subtree, titles, **kw):
        me = next(e for e in subtree if e["id"] == "f1")         # the model names features and blocks by alias
        assert me["blocks"][0]["id"] == "f1.1"
        return [{"kind": "amend", "feature_id": "f1",
                 "blocks": [{"id": "f1.1", "text": "Retries three times before giving up."}],
                 "rationale": "the retry count changed"}]

    cs = ChangeSet(modified=[chunk("c.py::send", "f3", "t1")])
    r = reflect(ws, cs, Graph(), propose=propose)
    assert not r.plans and not r.refused
    assert description(ws.store, "h1") == "Retries three times before giving up."
    assert phase(ws.store, "h1") == "synced"


def test_large_rewrite_and_held_feature_become_plans(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    big = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": "Entirely new prose about something else."}]}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py::send", "f3", "t1")]), Graph(), propose=big)
    assert len(r.plans) == 1
    assert [c.stage for c in plan_claims(ws.store, "h1")] and phase(ws.store, "h1") == "proposed"
    assert "Retries once" in description(ws.store, "h1")          # live shows both; the plan is gray
    # Reject it; the human then types (unsent ink) and a small repair must become a plan too.
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": r.plans[0]}, "payload": {}}], actor="human:local")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Fix this."}}],
              actor="human:local")
    small = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": ws.store.blocks["p1"].text().live_text().replace("once", "twice")}]}]
    r2 = reflect(ws, ChangeSet(modified=[chunk("c.py::send", "f4", "t1")]), Graph(), propose=small)
    assert len(r2.plans) == 1 and ws.store.plans[r2.plans[0]].note == "the document has pending intent"


def test_add_node_is_a_plan_with_its_bindings_and_coverage_net(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    added = [chunk("s.py::save", "f9", "t9", "def save(): ..."), chunk("s.py::load", "f8", "t8")]

    def propose(changes, subtree, titles, **kw):
        assert [a["symbol"] for a in changes["added"]] == ["s.py::save", "s.py::load"]
        return [{"kind": "add_node", "parent_id": "h1", "title": "Storage", "description": "Saves and loads state.",
                 "bindings": [["s.py", "s.py::save"]], "rationale": "no node covered persistence"}]

    r = reflect(ws, ChangeSet(added=added, rows=[]), Graph(), propose=propose)
    assert len(r.plans) == 2                      # the add plan, plus a coverage plan for the unplaced chunk
    plan = ws.store.plans[r.plans[0]]
    assert plan.kind == "add" and ws.store.blocks[plan.target].level == 2
    assert title(ws.store, plan.target) == "Storage"
    assert ws.store.bindings["s.py::save"].feature == plan.target
    assert any(ws.store.by_id[i].kind == K.BIND_ATTACH for i in plan.ops)   # the binding is part of the proposal
    # Accept: the node's words keep the loop as author and it is now a real feature.
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": plan.id}, "payload": {}}], actor="human:local")
    assert phase(ws.store, plan.target) == "synced"
    assert description(ws.store, plan.target) == "Saves and loads state."


def test_emptied_feature_becomes_delete_plan_and_accept_deletes(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    r = reflect(ws, ChangeSet(removed=[chunk("c.py::send", "f1", "t1")]), Graph(), propose=lambda *a, **k: [])
    assert r.auto["detach"] == 1 and len(r.plans) == 1
    p = ws.store.plans[r.plans[0]]
    assert p.kind == "delete" and p.target == "h1"
    assert [c.stage for c in plan_claims(ws.store, "h1")] == ["removed", "removed"]
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": p.id}, "payload": {}}], actor="human:local")
    assert not ws.store.blocks["h1"].live and not ws.store.blocks["p1"].live


def test_a_feature_emptied_while_the_person_typed_is_proposed_on_the_next_pass(tmp_path):
    """The delete proposal is state-based: held once, offered later, and never
    a second time once the person has answered it."""
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " Keeping this."}}], actor="human:local")
    r = reflect(ws, ChangeSet(removed=[chunk("c.py::send", "f1", "t1")]), Graph(), propose=lambda *a, **k: [])
    assert r.auto["detach"] == 1 and r.plans == []             # the person is mid-thought
    assert phase(ws.store, "h1") == "edited"
    # The person hands off and, since no bound symbol is left to move, marks it
    # done by hand (`codoc done`): the feature is no longer held.
    handoff(ws, "h1", actor="human:local")
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {}}], actor="human:local")
    assert phase(ws.store, "h1") == "synced"
    r = reflect(ws, ChangeSet(added=[chunk("n.py::x", "fz", "tz")]), Graph(), propose=lambda *a, **k: [])
    pid = next(p for p in r.plans if ws.store.plans[p].kind == "delete")
    assert ws.store.plans[pid].target == "h1"
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor="human:local")
    r = reflect(ws, ChangeSet(added=[chunk("n.py::y", "fy", "ty")]), Graph(), propose=lambda *a, **k: [])
    assert not any(ws.store.plans[p].target == "h1" for p in r.plans)   # answered once, not asked again


def test_a_symbol_an_open_plan_borrowed_does_not_make_its_owner_look_emptied(tmp_path):
    """A plan's `bind.attach` applies at once and displaces the prior owner
    (I8). An agent proposing a new feature that takes h1's only symbol leaves
    h1 with no binding, but h1 is not proposed for deletion while that plan is
    open. Rejecting the agent's plan gives the symbol back."""
    from codoc.loops.reflect import _emptied
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    assert _emptied(ws.store) == []
    pid = ws.open_plan(actor="agent:claude-code", kind="add", note="split the client")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h2"},
                "payload": {"kind": HEADING, "level": 1, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"},
                "payload": {"after": START, "text": "Sender"}},
               {"kind": K.BIND_ATTACH, "target": {"fid": "h2"},
                "payload": {"symbol": "c.py::send"}}],
              actor="agent:claude-code", plan=pid)
    assert ws.store.bindings["c.py::send"].feature == "h2"
    assert _emptied(ws.store) == [], "h1 is empty only while a proposal nobody answered says so"
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor="human:local")
    assert ws.store.bindings["c.py::send"].feature == "h1"
    assert _emptied(ws.store) == []


def test_rejecting_an_add_plan_detaches_its_bindings(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    propose = lambda c, s, t, **kw: [{"kind": "add_node", "parent_id": "h1", "title": "Storage", "description": "Saves.", "bindings": [["s.py", "s.py::save"]]}]
    r = reflect(ws, ChangeSet(added=[chunk("s.py::save", "f9", "t9")]), Graph(), propose=propose)
    pid = r.plans[0]
    fid = ws.store.plans[pid].target
    assert ws.store.bindings["s.py::save"].feature == fid
    rej = ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor="human:local")[0]
    assert not ws.store.blocks[fid].live and "s.py::save" not in ws.store.bindings
    ws.submit([dict(b) for b in rej.inverse], actor="human:local")           # reopen restores the binding
    assert ws.store.blocks[fid].live and ws.store.bindings["s.py::save"].feature == fid


def test_amend_is_one_unit_and_omission_means_unchanged(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": START, "text": "Still true."}}], actor="loop")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Mine."}}], actor="human:local")
    same = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": T(ws.store, "p1")}]}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py::send", "f3", "t1")]), Graph(), propose=same)
    assert r.plans == []                                                   # nothing to propose, no empty plan
    two = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1",
                                  "blocks": [{"id": "p1", "text": T(ws.store, "p1").replace("once", "twice")}, {"text": "New one."}, {"text": "New two."}]}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py::send", "f4", "t1")]), Graph(), propose=two)
    assert len(r.plans) == 1
    assert [c.text for c in plan_claims(ws.store, "h1") if c.stage == "removed"] == ["once"]   # p2 untouched
    assert len([b for b in ws.store.body("h1") if b.kind == PARAGRAPH]) == 4
    strike = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p2", "delete": True}]}]
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": r.plans[0]}, "payload": {}}], actor="human:local")
    r = reflect(ws, ChangeSet(modified=[chunk("c.py::send", "f5", "t1")]), Graph(), propose=strike)
    assert len(r.plans) == 1 and [c.text for c in plan_claims(ws.store, "h1") if c.stage == "removed"] == ["Still true."]


def test_model_may_not_attach_a_symbol_the_index_removed_and_bad_shapes_are_refused(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": HEADING, "level": 1, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "Other"}}], actor="loop")
    propose = lambda c, s, t, **kw: [{"kind": "attach", "feature_id": "h2", "bindings": ["c.py::send"]},
                                     {"kind": "amend", "feature_id": "h1", "blocks": "not a list"},
                                     {"kind": "add_node"}, "junk"]
    cs = ChangeSet(removed=[chunk("c.py::send", "f1", "t1")], added=[chunk("n.py::x", "fz", "tz")])
    r = reflect(ws, cs, Graph(), propose=propose, allow_delete=False)
    assert "c.py::send" not in ws.store.bindings
    assert r.refused == [("attach", "no bindable symbol"), ("amend", "amend.blocks must be a list of objects"),
                         ("add_node", "add_node.title must be a non-empty string"), ("", "not an object")]
    assert len(r.plans) == 1                                               # the coverage net still placed n.py::x


def test_held_amend_that_unbolds_keeps_the_bold_in_the_rejected_view_and_export(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    ins = [o for o in ws.store.ops if o.kind == K.TEXT_INSERT and o.payload["bid"] == "p1"][0]
    ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 11], "on": True}}], actor="human:local")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " Mine."}}], actor="human:local")
    unbold = lambda c, s, t, **kw: [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": "Retries once before giving up. Mine."}]}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py::send", "f3", "t1")]), Graph(), propose=unbold)
    assert len(r.plans) == 1 and ws.store.plans[r.plans[0]].status == "open"
    assert markdown(ws.store.blocks["p1"].text(), "rejected", ws.store) == "Retries **once** before giving up. Mine."
    assert "**once**" in export_markdown(ws.store)
    assert [(c.stage, c.text) for c in plan_claims(ws.store, "h1") if c.stage in ("marked", "unmarked")] == [("unmarked", "bold:once")]
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": r.plans[0]}, "payload": {}}], actor="human:local")
    assert md_of(ws.store, "p1") == "Retries **once** before giving up. Mine."


def test_emptied_covers_a_feature_whose_last_binding_was_displaced(tmp_path):
    """`_emptied` reads state: any live feature that once had code and has
    none now. A feature whose only symbol was attached to another feature (I8
    displacement) has none and never saw a `bind.detach`."""
    from codoc.loops.reflect import _emptied
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": HEADING, "level": 1, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "Server"}}], actor=H)
    ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h2"}, "payload": {"symbol": "c.py::send", "fingerprint": "f1"}}], actor="loop")
    assert not any(b.feature == "h1" for b in ws.store.bindings.values())
    assert "h1" in _emptied(ws.store)
    ws.close()


def test_amend_lands_one_plan_when_the_second_block_flips_the_gate(tmp_path):
    """One amend is one plan. Block 1 is a repair small enough for the trunk;
    block 2 rewrites a person's paragraph past the gate. Both ride under one
    plan and nothing lands on the trunk."""
    from codoc.loops.edits import Compiler
    from codoc.loops.reflect import ReflectResult, _land, _Pass
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": START, "text": "Then it gives up for good."}}], actor=H)
    res = ReflectResult()
    p = _Pass(ws, res, {})
    _land(p, Compiler(ws.store).compile({"kind": "amend", "feature_id": "h1", "blocks": [
        {"id": "p1", "text": "Retries twice."},
        {"id": "p2", "text": "Completely different words here now."}]}))
    plans = [pl for pl in ws.store.plans.values() if pl.status == "open"]
    assert len(plans) == 1, [pl.id for pl in plans]
    assert all(o.plan == plans[0].id for o in res.ops if o.kind.startswith("text.")), [(o.kind, o.plan) for o in res.ops]
    ws.close()


def test_the_loop_reads_its_own_open_plan_and_adds_to_it(tmp_path):
    """The loop reads a feature with its own open plan's words, so the next
    pass over the same code does not propose the same block again, and a
    further repair there, a new block or a rewording of the plan's own words,
    joins that plan: one verdict takes all of it. A rewording of the trunk's
    words still lands on the trunk."""
    from codoc.loops.context import description_blocks
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    passes = iter(range(2, 99))

    def run(edit):
        """One pass; ``edit(shown)`` answers from the blocks the model was shown."""
        def propose(c, subtree, t, **kw):
            return edit({b["id"]: b["text"] for e in subtree if e["id"] == "f1" for b in e["blocks"]})
        return reflect(ws, ChangeSet(modified=[chunk("c.py::send", f"f{next(passes)}", "t1")]), Graph(),
                       propose=propose)

    def add(bullet):
        return lambda shown: [] if f"- {bullet}" in shown.values() else [
            {"kind": "amend", "feature_id": "h1", "blocks": [{"text": f"- {bullet}"}], "rationale": bullet}]

    first = run(add("Gives up on `None`."))
    pid = first.plans[0]
    again = run(add("Gives up on `None`."))
    assert len(first.plans) == 1 and again.plans == [] and not again.refused
    later = run(add("Waits 2s between tries."))
    bullet = next(b["id"] for b in description_blocks(ws.store, "h1", mode="loop") if "None" in b["text"])
    reworded = run(lambda shown: [{"kind": "amend", "feature_id": "h1",
                                   "blocks": [{"id": bullet, "text": "- Gives up on `None` or an empty reply."}]}])
    trunk = run(lambda shown: [{"kind": "amend", "feature_id": "h1", "blocks": [{"id": "p1", "text": "Retries twice before giving up."}]}])
    for res in (later, reworded, trunk):
        assert res.plans == [] and not res.refused, (res.plans, res.refused)
    assert [p.id for p in ws.store.open_plans.values()] == [pid]
    assert {o.plan for o in later.ops + reworded.ops if o.kind.startswith(("block.", "text."))} == {pid}
    assert ws.store.plans[pid].note == "Gives up on `None`."
    assert T(ws.store, "p1") == "Retries twice before giving up."
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor=H)
    assert description(ws.store, "h1").splitlines() == [
        "Retries twice before giving up.", "", "- Gives up on `None` or an empty reply.", "- Waits 2s between tries."]


def test_the_model_sees_aliases_and_its_aliases_are_mapped_back(tmp_path):
    """The pass shows `f1` and `f1.1`, never a block id, and compiles an answer
    in those names against the real ids. An alias that names nothing is a
    recorded refusal."""
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    shown = []

    def propose(changes, subtree, titles, **kw):
        shown.append((changes, subtree, titles))
        return [{"kind": "amend", "feature_id": "f1", "blocks": [{"id": "f1.1", "text": "Retries twice before giving up."}]},
                {"kind": "attach", "feature_id": "f12", "bindings": ["c.py::send"]}]

    r = reflect(ws, ChangeSet(modified=[chunk("c.py::send", "f2", "t1")]), Graph(), propose=propose)
    changes, subtree, titles = shown[0]
    assert "b-" not in repr(shown) and "h1" not in repr(shown)
    assert (titles[0]["id"], subtree[0]["id"], changes["modified"][0]["current_feature_id"]) == ("f1", "f1", "f1")
    assert T(ws.store, "p1") == "Retries twice before giving up."
    assert r.refused == [("attach", "unknown feature 'f12'")]


def test_a_plan_note_names_a_feature_by_title_not_by_alias(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)

    def propose(changes, subtree, titles, **kw):
        return [{"kind": "add_node", "parent_id": "f1", "title": "Storage", "description": "Saves state.",
                 "bindings": ["s.py::save"], "rationale": "把 f1.1 的保存部分拆出来，放在f1下"}]

    cs = ChangeSet(added=[chunk("s.py::save", "f9", "t9")], modified=[chunk("c.py::send", "f2", "t1")])
    r = reflect(ws, cs, Graph(), propose=propose)
    assert ws.store.plans[r.plans[0]].note == "把 Client 的保存部分拆出来，放在Client下"


def test_a_chunk_back_at_its_bound_fingerprint_asks_the_model_nothing(tmp_path):
    """Checking a branch out again changes the index, not the code the binding was refreshed on."""
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)                                     # c.py::send bound at fingerprint f1
    r = reflect(ws, ChangeSet(modified=[chunk("c.py::send", "f1", "t1")]), Graph(),
                propose=lambda *a, **k: [{"kind": "amend", "feature_id": "f1", "title": "Changed"}])
    assert r.llm_calls == 0 and not r.ops


def test_the_reflect_line_reports_what_its_calls_cost(tmp_path, monkeypatch):
    from codoc.llm import provider
    from tests.helpers import fake_claude
    fake_claude(monkeypatch, {"result": "[]", "total_cost_usd": 0.0031})
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    r = reflect(ws, ChangeSet(modified=[chunk("c.py::send", "f2", "t1")]), Graph(),
                propose=lambda *a, **k: provider.complete("p", provider.LLMConfig("claude", "haiku")) and [])
    assert "1 LLM call(s), $0.0031" in r.summary()
