"""The roles table and the policy: who may write what, what a suggester's
writes become, and that replay keeps what was recorded."""
from codoc.calculus import ops as K
from codoc.calculus.derive import markdown
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.ids import START
from codoc.calculus.store import Store
from codoc.calculus.text import SUGGEST_DEL
from codoc.workspace import Roles, Workspace
from codoc.workspace import log as L
from tests.helpers import H, T, ins, refs, seed, seed_loop, sub, write_roles


def test_agents_write_no_verdicts_and_suggesters_ride_only_their_own_plans(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": HEADING, "level": 1, "after": START}},
               {"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": "h1"}}], actor="human:local")
    pid = ws.open_plan(actor="loop", note="the loop's repair")
    denied = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}},
                        {"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {}},
                        {"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor="agent:claude-code")
    assert [o.conflict for o in denied] == ["authority"] * 3
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor="human:local")
    assert ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {}}], actor="loop")[0].conflict is None
    smuggled = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "x"}}],
                         actor="human:contrib", plan=pid)[0]
    assert smuggled.conflict == "authority" and smuggled.id not in ws.store.plans[pid].ops
    own = ws.open_plan(actor="human:contrib")
    ok = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "x"}}],
                   actor="human:contrib", plan=own)[0]
    assert ok.conflict is None


def test_roles_table_over_every_kind():
    roles = Roles({"human:local": "owner", "human:m": "maintainer"})
    from codoc.calculus.ops import Op
    store = Store()
    store.plans["p-mine"] = __import__("codoc.calculus.store", fromlist=["Plan"]).Plan("p-mine", "human:s")
    store.plans["p-theirs"] = __import__("codoc.calculus.store", fromlist=["Plan"]).Plan("p-theirs", "loop")

    def allowed(actor, kind, plan=None):
        return roles.policy(store, Op(id="x:1", at="", actor=actor, batch="", kind=kind, plan=plan)) is None

    for kind in K.KINDS:
        assert allowed("human:local", kind) and allowed("human:m", kind)
        assert allowed("agent:a", kind) == (kind not in K.VERDICTS)
        assert allowed("loop", kind) == (kind not in K.VERDICTS or kind == K.FEATURE_REALIZED)
        assert allowed("human:s", kind) == (kind == K.PLAN_OPEN)
        assert allowed("human:s", kind, "p-mine")            # the policy allows; apply's not_plannable decides the rest
        assert not allowed("human:s", kind, "p-theirs") or kind == K.PLAN_OPEN


def test_roles_from_config_make_every_clone_agree(tmp_path):
    from codoc.workspace.config import write_config
    cd = tmp_path / ".codoc"
    cd.mkdir()
    write_config(cd, roles={"human:alice": "maintainer"})
    ws = Workspace(cd)
    seed_loop(ws)
    pid = ws.open_plan(actor="loop")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "P "}}], actor="loop", plan=pid)
    assert ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:alice")[0].conflict is None
    assert ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:bob")[0].conflict == "authority"
    ws.close()
    ws2 = Workspace(cd)
    assert ws2.roles.role("human:alice") == "maintainer" and ws2.store.state_fingerprint() == ws.store.state_fingerprint()
    assert Roles.load(cd).role("human:local") == "owner"


def test_a_later_demotion_does_not_rewrite_what_was_written(tmp_path):
    from codoc.workspace.config import write_config
    cd = tmp_path / ".codoc"
    cd.mkdir()
    write_config(cd, roles={"human:alice": "maintainer"})
    ws = Workspace(cd)
    seed_loop(ws)
    pid = ws.open_plan(actor="loop")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "P "}}], actor="loop", plan=pid)
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:alice")[0]
    bad = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:bob")[0]
    assert acc.conflict is None and bad.conflict == "authority"
    fp = ws.store.state_fingerprint()
    ws.close()
    write_config(cd, roles={"human:alice": "suggest"})       # alice's access is reduced later
    ws2 = Workspace(cd)
    assert ws2.store.by_id[acc.id].conflict is None and ws2.store.by_id[bad.id].conflict == "authority"
    assert ws2.store.state_fingerprint() == fp
    # …and the new table is what judges the next op she writes.
    assert ws2.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor="human:alice")[0].conflict == "authority"


def test_demotion_after_the_fact_does_not_rewrite_history(tmp_path):
    """A recorded success replays as a success, whatever the table says now."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    write_roles(cd, {"human:gh:alice": "maintainer"})
    ws.roles = Roles.load(cd); ws.store.policy = ws.roles.policy
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    ins(ws, "p1", list(refs(ws, "p1")[-1]), " Proposed.", actor="loop", plan=pid)
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:gh:alice")[0]
    assert acc.conflict is None
    fp = ws.store.state_fingerprint()
    ws.close()
    write_roles(cd, {"human:gh:alice": "suggest"})
    ws2 = Workspace(cd, readonly=True)
    assert ws2.store.plans[pid].status == "accepted"
    assert ws2.store.state_fingerprint() == fp


def test_recorded_conflict_replays_as_that_conflict_and_fingerprint_holds(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    bad = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:gh:bob")[0]
    assert bad.conflict == "authority"
    # an unknown-anchor conflict too
    ua = ins(ws, "p1", ["s-nope:1", 0], "x")
    assert ua.conflict == "unknown_anchor"
    fp = ws.store.state_fingerprint()
    ws.close()
    write_roles(cd, {"human:gh:bob": "owner"})       # promoted later: history does not change
    ws2 = Workspace(cd, readonly=True)
    assert ws2.store.by_id[bad.id].conflict == "authority"
    assert ws2.store.by_id[ua.id].conflict == "unknown_anchor"
    assert ws2.store.plans[pid].status == "open"
    assert ws2.store.state_fingerprint() == fp


def test_a_suggesters_backspace_becomes_a_suggest_del_under_their_plan(tmp_path):
    """A suggester's edit on the trunk is applied as a plan of theirs. A plan
    proposes and never kills, so their range `text.delete` becomes the plan's
    `suggest_del` over the range, as `as_plan_bodies` does for an ingest."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    L.submit(cd, "hub-alice", [sub("d1", K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": list(r[0]), "to": list(r[6])})])
    ops = ws.drain_inbox()
    assert [o.conflict for o in ops if o.kind != K.PLAN_OPEN] == [None], [(o.kind, o.conflict) for o in ops]
    assert T(ws.store, "p1") == "Retries once."                       # proposed, not destroyed
    t = ws.store.blocks["p1"].text()
    sd = [m for m in t.marks if m.kind == SUGGEST_DEL]
    assert sd and ws.store.plans[sd[0].attrs["plan"]].actor == "human:gh:alice", [m.to_json() for m in t.marks]
    ws.close()


def test_a_suggesters_segments_delete_strikes_only_the_characters_it_names(tmp_path):
    """The editor sends the exact characters it deleted. As a proposal, the
    strike covers those and not a word the owner typed between them since."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)                          # "Retries once."
    r = refs(ws, "p1")
    seen = r[0][0]
    L.submit(cd, "hub-alice", [sub("d1", K.TEXT_DELETE, {"bid": "p1", "path": "text"},
                                    {"segments": [[seen, r[0][1], 8]]})])           # "Retries "
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(r[3]), "text": "ALWAYS"}}], actor=H)     # inside the range, unseen by alice
    ops = ws.drain_inbox()
    mark = next(o for o in ops if o.kind == K.TEXT_MARK)
    assert mark.conflict is None and mark.plan
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": mark.plan}, "payload": {}}], actor=H)
    assert T(ws.store, "p1") == "ALWAYSonce."
    ws.close()


def test_a_contributor_backspacing_over_their_own_proposal_removes_it(tmp_path):
    """A suggester's range delete over only their own proposed words removes
    them: typing ' More.' and backspacing over it leaves nothing to accept. A
    range that also covers a word of the trunk's is still a proposal."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    L.submit(cd, "hub-alice", [sub("i1", K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": list(r[-1]), "text": " More."})])
    ins = [o for o in ws.drain_inbox() if o.kind == K.TEXT_INSERT][0]
    assert ins.conflict is None and ins.plan and T(ws.store, "p1") == "Retries once. More."
    L.submit(cd, "hub-alice", [sub("d1", K.TEXT_DELETE, {"bid": "p1", "path": "text"},
                                    {"from": [ins.id, 0], "to": [ins.id, 5]}, batch="b2")])
    ops = ws.drain_inbox()
    assert all(o.conflict is None for o in ops), [(o.kind, o.conflict) for o in ops]
    assert T(ws.store, "p1") == "Retries once."
    assert not [m for m in ws.store.blocks["p1"].text().marks if m.kind == SUGGEST_DEL]
    # …but a range that also covers a word of the trunk's is still a proposal.
    L.submit(cd, "hub-alice", [sub("d2", K.TEXT_DELETE, {"bid": "p1", "path": "text"},
                                    {"from": list(r[0]), "to": list(r[6])}, batch="b3")])
    ws.drain_inbox()
    assert T(ws.store, "p1") == "Retries once."
    assert [m.kind for m in ws.store.blocks["p1"].text().marks] == [SUGGEST_DEL]
    ws.close()


def test_a_contributor_cannot_revive_words_the_maintainer_deleted(tmp_path):
    """A restore is judged by the delete it undoes, not by who minted the
    characters. A maintainer removing a plan's words is an ordinary trunk
    delete, and a proposal does not get to answer it.

    Everything a suggester writes is tagged with their plan, and the applier
    holds a tagged restore to a delete of the same plan, so the refusal is
    `not_this_plans_delete`, not `authority`. The policy reads plans, not the
    document, so `codoc validate-logs` can judge a foreign log the same way."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    L.submit(cd, "hub-alice", [sub("i1", K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": list(r[-1]), "text": " More."})])
    ins = [o for o in ws.drain_inbox() if o.kind == K.TEXT_INSERT][0]
    gone = ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"},
                       "payload": {"from": [ins.id, 0], "to": [ins.id, 5]}}], actor=H)[0]
    assert T(ws.store, "p1") == "Retries once."
    L.submit(cd, "hub-alice", [sub("u1", K.TEXT_RESTORE, {"bid": "p1", "path": "text"},
                                    {"segments": [[ins.id, 0, 6]], "by": gone.id}, batch="b2")])
    assert [o.conflict for o in ws.drain_inbox()] == ["not_this_plans_delete"]
    assert T(ws.store, "p1") == "Retries once."
    # …while undoing a delete of THEIR own stands, which is what it has to keep.
    L.submit(cd, "hub-alice", [sub("i2", K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": list(r[-1]), "text": " Twice."}, batch="b3")])
    second = [o for o in ws.drain_inbox() if o.kind == K.TEXT_INSERT][0]
    L.submit(cd, "hub-alice", [sub("d2", K.TEXT_DELETE, {"bid": "p1", "path": "text"},
                                    {"from": [second.id, 0], "to": [second.id, 6]}, batch="b4")])
    theirs = [o for o in ws.drain_inbox() if o.kind == K.TEXT_DELETE][0]
    assert theirs.conflict is None and T(ws.store, "p1") == "Retries once."
    undo = theirs.inverse[0]                            # exactly what the editor's undo sends
    L.submit(cd, "hub-alice", [sub("u2", undo["kind"], undo["target"], undo["payload"], batch="b5")])
    assert [o.conflict for o in ws.drain_inbox()] == [None]
    assert T(ws.store, "p1") == "Retries once. Twice."
    ws.close()


def test_a_suggesters_no_op_ingest_leaves_no_empty_open_plan(tmp_path):
    """The drain opens the suggester's plan before it knows whether anything
    will land under it, and withdraws it when nothing did."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    L.submit(cd, "hub-alice", [sub("i1", "ingest", {"bid": "p1", "path": "text"},
                                    {"base_text": "Retries once.", "new_md": "Retries once."})])
    ws.drain_inbox()
    empty = [p for p in ws.store.plans.values() if p.status == "open" and not p.ops]
    assert not empty, [(p.id, p.actor, p.ops) for p in empty]
    # and a batch whose only op conflicts (unknown anchor)
    L.submit(cd, "hub-alice", [sub("x1", K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": ["s-nope:1", 0], "text": "x"}, batch="b2")])
    ws.drain_inbox()
    empty = [p for p in ws.store.plans.values() if p.status == "open" and not p.ops]
    assert not empty, [(p.id, p.actor, p.ops) for p in empty]
    ws.close()


def test_a_contributor_typing_over_several_settles_leaves_one_proposal(tmp_path):
    """The editor flushes every 350 ms. A plan per flush would leave the
    maintainer four verdicts to give on one sentence."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    from codoc.workspace.config import write_config
    write_config(cd, roles={"human:gh:alice": "suggest"})
    ws.roles = Roles.load(cd)
    ws.store.policy = ws.roles.policy
    seed(ws)
    for i, word in enumerate(["One ", "two ", "three "]):
        last = ws.store.blocks["p1"].text().last_live_ref()
        L.submit(cd, "hub-alice", [sub(f"c{i}", K.TEXT_INSERT, {"bid": "p1", "path": "text"},
                                        {"after": list(last), "text": word}, batch=f"t{i}")])
        assert all(o.conflict is None for o in ws.drain_inbox() if o.kind != K.PLAN_OPEN)
    open_plans = [p for p in ws.store.plans.values() if p.status == "open"]
    assert len(open_plans) == 1, [(p.id, p.ops) for p in open_plans]
    assert len(open_plans[0].ops) == 3
    ws.close()


def test_a_contributor_cannot_revive_words_they_withdrew_once_the_plan_is_decided(tmp_path):
    """Propose, withdraw, wait for the verdict, restore: the restore
    conflicts. Everything a suggester writes rides in their plan, and a decided
    plan takes no more ops, so the words stay dead after an accept and after a
    reject, and the `rejected` read mode does not show them."""
    for verdict in (K.PLAN_ACCEPT, K.PLAN_REJECT):
        cd = tmp_path / verdict / ".codoc"
        ws = Workspace(cd); seed(ws)
        r = refs(ws, "p1")
        L.submit(cd, "hub-alice", [sub("i1", K.TEXT_INSERT, {"bid": "p1", "path": "text"},
                                        {"after": list(r[-1]), "text": " More."})])
        ins = [o for o in ws.drain_inbox() if o.kind == K.TEXT_INSERT][0]
        L.submit(cd, "hub-alice", [sub("d1", K.TEXT_DELETE, {"bid": "p1", "path": "text"},
                                        {"segments": [[ins.id, 0, 6]]}, batch="b2")])
        gone = [o for o in ws.drain_inbox() if o.kind == K.TEXT_DELETE][0]
        assert gone.conflict is None and gone.plan == ins.plan   # their own words, in their own plan
        ws.submit([{"kind": verdict, "target": {"pid": ins.plan}, "payload": {}}], actor=H)
        L.submit(cd, "hub-alice", [sub("u1", K.TEXT_RESTORE, {"bid": "p1", "path": "text"},
                                        {"segments": [[ins.id, 0, 6]], "by": gone.id}, batch="b3")])
        after = ws.drain_inbox()
        assert all(o.conflict for o in after), [(o.kind, o.conflict) for o in after]
        assert T(ws.store, "p1") == "Retries once."
        assert markdown(ws.store.blocks["p1"].text(), "rejected", ws.store) == "Retries once."
        ws.close()


def test_a_contributors_typo_corrected_within_one_flush_is_not_struck_as_a_proposal(tmp_path):
    """The editor batches a flush, so an insert and the Backspace over it
    arrive together when somebody corrects a typo, with `$cid` refs naming an
    op the daemon has not minted yet. The drain resolves those refs before it
    asks whether the range is the suggester's own words, so the correction
    removes the typo instead of striking it."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    L.submit(cd, "hub-alice", [
        sub("c1", K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": list(r[-1]), "text": " Moer"}),
        sub("c2", K.TEXT_DELETE, {"bid": "p1", "path": "text"},
             {"from": ["$cid", "c1", 0], "to": ["$cid", "c1", 4]}),
    ])
    ops = ws.drain_inbox()
    assert all(o.conflict is None for o in ops), [(o.kind, o.conflict) for o in ops]
    assert T(ws.store, "p1") == "Retries once."
    assert not ws.store.blocks["p1"].text().proposals(kind=SUGGEST_DEL)
    ws.close()


def test_a_client_cannot_send_fields_only_a_snapshot_writes(tmp_path):
    """A hub client claims the owner's insert as a plan op, adds a block whose
    text is pre-attributed to the owner, and restores a bold the owner never
    placed. None of it holds once the owner rejects the open plans."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    owner = next(o for o in ws.store.ops if o.kind == K.TEXT_INSERT and o.target["bid"] == "p1")
    forged = {"segs": [{"op": owner.id, "k0": 100, "chars": "Also drops the table.", "actor": H,
                        "at": "00000000000000000001-00000000000000000000-x"}], "marks": []}
    L.submit(cd, "hub-mallory", [
        sub("c1", K.PLAN_OPEN, {"pid": "p-forged000001"}, {"kind": "edit", "target": "p1", "ops": [owner.id]}),
        sub("c2", K.BLOCK_ADD, {"bid": "b-forged000002"},
            {"kind": PARAGRAPH, "after": "p1", "texts": {"text": forged}}),
        sub("c3", K.TEXT_MARK, {"bid": "p1", "path": "text"},
            {"kind": "bold", "start": [owner.id, 0], "end": [owner.id, 6], "on": True, "actor": H}),
    ])
    ws.drain_inbox()
    assert ws.store.plans["p-forged000001"].ops == []
    assert ws.store.blocks["b-forged000002"].text().live_text() == ""
    for pid in list(ws.store.open_plans):
        ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor=H)
    assert T(ws.store, "p1") == "Retries once."
    assert ws.store.blocks["p1"].text().marks == []
    ws.close()


def test_validate_logs_flags_a_snapshot_only_field_in_a_session_file(tmp_path):
    from codoc.workspace.validate import validate_logs
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    ws.submit([{"kind": K.PLAN_OPEN, "target": {"pid": "p-x"}, "payload": {"kind": "edit", "ops": ["s-other:1"]}}],
              actor=H)
    ws.close()
    found = validate_logs(L.list_sessions(cd), roles=Roles({}))
    assert any("plan.open carries ops" in f.reason for f in found), [str(f) for f in found]
