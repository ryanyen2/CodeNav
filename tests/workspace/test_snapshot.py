"""Snapshots: a fold keeps every view of the document, and work in flight
stays in the log."""
from codoc.calculus import ops as K
from codoc.calculus.derive import accept_handoffs, phase
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.ids import START
from codoc.calculus.text import BOLD, SUGGEST_DEL
from codoc.index.graph import Graph
from codoc.loops.bootstrap import bootstrap
from codoc.loops.realize import directive
from codoc.workspace import Roles, Workspace
from codoc.workspace import log as L
from codoc.workspace.snapshot import write_snapshot
from tests.helpers import ROWS, H, T, fake_file, fake_org, fold, ins, kind_block, mark, refs, seed, ws_paragraph


def test_snapshot_keeps_the_fingerprint_when_a_dead_block_exists(tmp_path):
    """`state_fingerprint` is equal across a fold. A deleted paragraph is the
    ordinary case."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": START, "text": "gone soon"}}], actor=H)
    ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "p2"}, "payload": {}}], actor=H)
    fp = ws.store.state_fingerprint()
    ws.close()
    ws = Workspace(cd)
    write_snapshot(cd, ws.store, ws.clock)
    ws.close()
    ws2 = Workspace(cd, readonly=True)
    assert ws2.store.state_fingerprint() == fp


def test_snapshot_keeps_a_plugin_blocks_layout(tmp_path, monkeypatch):
    """A fold re-adds each block with the layout it was added with, so a clone
    without the kind's code rebuilds its Text fields from the snapshot."""
    from codoc.calculus import doc
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    monkeypatch.setitem(doc._REGISTERED, "widget", doc.Layout(("caption",)))
    w = kind_block(ws, "widget", {"url": "u", "caption": "A **plot**"}, after="p1")
    fp = ws.store.state_fingerprint()
    ws.close()
    fold(cd)
    assert '"layout"' in next((cd / "log").glob("snapshot-*.jsonl")).read_text()
    monkeypatch.delitem(doc._REGISTERED, "widget")
    st = Workspace(cd, readonly=True).store
    assert st.state_fingerprint() == fp and st.blocks[w].layout.text == ("caption",)


def test_snapshot_keeps_an_accepted_delete_plan(tmp_path):
    """The fold carries plans with their status. An accepted `kind=delete`
    plan whose target is dead comes back accepted, not as a conflict that
    leaves the plan behind."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": HEADING, "level": 1, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "Old"}}], actor=H)
    pid = ws.open_plan(actor="loop", kind="delete", target="h2")
    r = refs(ws, "h2")
    mark(ws, "h2", SUGGEST_DEL, r[0], r[-1], actor="loop", plan=pid)
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor=H)[0]
    assert acc.conflict is None and not ws.store.blocks["h2"].live
    fp = ws.store.state_fingerprint()
    ws.close()
    ws = Workspace(cd); write_snapshot(cd, ws.store, ws.clock); ws.close()
    ws2 = Workspace(cd, readonly=True)
    assert pid in ws2.store.plans and ws2.store.plans[pid].status == "accepted"
    assert not ws2.store.blocks["h2"].live
    assert ws2.store.state_fingerprint() == fp
    assert not [p for p in ws2.problems], ws2.problems


def test_a_plan_that_asks_for_a_build_still_does_after_a_fold(tmp_path):
    """The fold archives the plan's opening op; accepting the plan again (after
    undoing the verdict) must still hand its feature off."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.PLAN_OPEN, "target": {"pid": "pb"},
                "payload": {"kind": "edit", "target": "p1", "build": True}}], actor="agent:claude-code")
    ins(ws, "p1", list(refs(ws, "p1")[-1]), " Proposed.", actor="agent:claude-code", plan="pb")
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": "pb"}, "payload": {}}], actor=H)
    ws.close()
    ws = Workspace(cd); write_snapshot(cd, ws.store, ws.clock); ws.close()
    ws2 = Workspace(cd, readonly=True)
    assert ws2.store.plans["pb"].build
    assert accept_handoffs(ws2.store, ws2.store.plans["pb"]) == ["h1"]


def test_a_verdict_after_a_snapshot_still_acts_on_the_plans_archived_ops(tmp_path):
    """A plan open across a fold can still be rejected: its inserted words go,
    and its struck words come back unstruck."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    r = refs(ws, "p1")
    ins(ws, "p1", list(r[-1]), " Proposed words.", actor="loop", plan=pid)
    mark(ws, "p1", SUGGEST_DEL, r[0], r[6], actor="loop", plan=pid)       # strike "Retries"
    ws.close()
    ws = Workspace(cd); write_snapshot(cd, ws.store, ws.clock); ws.close()
    ws2 = Workspace(cd)
    assert ws2.store.plans[pid].status == "open"
    rej = ws2.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor=H)[0]
    assert rej.conflict is None
    assert T(ws2.store, "p1") == "Retries once.", T(ws2.store, "p1")
    assert not [m for m in ws2.store.blocks["p1"].text().marks if m.kind == SUGGEST_DEL]
    ws2.close()


def test_accept_after_a_snapshot_strikes_the_words_the_plan_proposed_to_delete(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    r = refs(ws, "p1")
    mark(ws, "p1", SUGGEST_DEL, r[0], r[6], actor="loop", plan=pid)       # strike "Retries"
    ws.close()
    ws = Workspace(cd); write_snapshot(cd, ws.store, ws.clock); ws.close()
    ws2 = Workspace(cd)
    acc = ws2.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor=H)[0]
    assert acc.conflict is None
    assert T(ws2.store, "p1") == " once.", T(ws2.store, "p1")
    ws2.close()


def test_an_undo_of_a_pre_snapshot_delete_still_restores_the_block(tmp_path):
    """A branch forked before the snapshot still merges. Its undo of a delete
    the snapshot folded is `block.restore {ids, by: <the delete>}`, and it
    restores the block."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p2", "path": "text"}, "payload": {"after": START, "text": "keep me"}}], actor=H)
    d = ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "p2"}, "payload": {}}], actor=H)[0]
    inverse = [dict(b) for b in d.inverse]
    ws.close()
    ws = Workspace(cd); write_snapshot(cd, ws.store, ws.clock); ws.close()
    ws2 = Workspace(cd)
    undo = ws2.submit(inverse, actor=H)[0]
    assert undo.conflict is None
    assert ws2.store.blocks["p2"].live, undo.payload
    ws2.close()


def test_a_phantom_with_delete_code_can_still_settle_after_a_fold(tmp_path):
    """A deleted bound feature handed off with delete_code settles when the
    pass sees its code removed, also when a fold lands between the hand-off and
    the build: the dead heading keeps the bindings it owned as `detached`."""
    from codoc.index.changes import ChangeSet, ChunkRef
    from codoc.index.graph import Graph
    from codoc.loops import realize as R
    from codoc.loops.reflect import reflect
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor=H)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"delete_code": True}}], actor=H)
    ws.close()
    fold(cd)
    ws = Workspace(cd)
    assert R.queued(ws.store) == ["h1"]
    cs = ChangeSet(removed=[ChunkRef("c.py", "c.py::send", "f1", "t1")])
    res = reflect(ws, cs, Graph(), propose=lambda *a, **k: [], under_epoch=True)
    R.settle(ws, res, sha="abc", files=["c.py"])
    assert R.queued(ws.store) == [], "the phantom's code went and it is still queued"
    ws.close()


def test_the_directive_of_a_queued_feature_is_the_same_across_a_fold(tmp_path):
    """A queued feature's directive (Focus from the person's bold, `removed:`
    from the person's deletion) is a function of state, so it is the same after
    the fold archives the ops that wrote those lines."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    mark(ws, "p1", BOLD, r[0], r[6])                                              # Focus: "Retries"
    ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"},
                "payload": {"from": list(r[8]), "to": list(r[11])}}], actor=H)     # removed: "once"
    ins(ws, "p1", list(r[12]), " Twice.", actor=H)                                # added: " Twice."
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"asked": "please"}}], actor=H)
    d1 = directive(ws.store, "h1")
    assert 'Focus: "Retries"' in d1 and 'removed: "once"' in d1 and "Author asked: please" in d1, d1
    ws.close()
    fold(cd)
    ws = Workspace(cd, readonly=True)
    d2 = directive(ws.store, "h1")
    assert d2 == d1, f"\nBEFORE:\n{d1}\n\nAFTER:\n{d2}"


def test_emptied_is_the_same_state_before_and_after_a_fold(tmp_path):
    """Whether a feature once had code is `bound_once`, which the fold keeps,
    so `_emptied` finds the same features after a fold."""
    from codoc.loops.reflect import _emptied
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.BIND_DETACH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": "c.py::send"}}], actor="loop")
    assert _emptied(ws.store) == ["h1"]
    ws.close()
    fold(cd)
    ws = Workspace(cd, readonly=True)
    assert _emptied(ws.store) == ["h1"], _emptied(ws.store)


def _folded(cd) -> tuple[int, int]:
    """(ops still in the log, ops the snapshot folded)."""
    return (sum(len(L.read_session(p).ops) for p in L.list_sessions(cd)),
            sum(len(L.read_session(p).ops) for p in L.list_snapshots(cd)))


def test_a_settled_tree_folds_whole_and_its_log_is_archived(tmp_path):
    """A tree with no open plan folds whole, and the session files its writers
    closed are archived."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor=H)
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {}}], actor="loop")
    fp = ws.store.state_fingerprint()
    ws.close()
    fold(cd)
    assert not L.list_sessions(cd), "a settled tree's log was not archived"
    ws2 = Workspace(cd, readonly=True)
    assert ws2.store.state_fingerprint() == fp and not ws2.problems


def test_the_fold_stops_where_the_work_in_flight_begins(tmp_path):
    """An open plan's verdict acts on the plan's own ops, so the fold stops at
    the first op of a feature with an open plan and leaves the rest as log. The
    settled feature before it folds, and its directive is unchanged."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor=H)
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {}}], actor="loop")
    # A second feature, and a proposal on it that nobody has answered.
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": HEADING, "level": 1, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "Server"}}], actor=H)
    pid = ws.open_plan(actor="loop", kind="edit", target="h2")
    ins(ws, "h2", list(refs(ws, "h2")[-1]), " (proposed)", actor="loop", plan=pid)
    before = directive(ws.store, "h1")
    ws.close()
    fold(cd)
    log, snap = _folded(cd)
    assert log and snap, f"folded {snap} ops, left {log} — one of the two halves is empty"
    ws2 = Workspace(cd)
    assert not ws2.problems
    # The proposal is still answerable, and answering it takes its own ops.
    assert ws2.store.plans[pid].status == "open"
    rej = ws2.submit([{"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}], actor=H)[0]
    assert rej.conflict is None and "(proposed)" not in T(ws2.store, "h2")
    assert directive(ws2.store, "h1") == before
    ws2.close()


def test_a_fold_keeps_who_answered_and_who_asked(tmp_path):
    """The fold re-emits a decided plan's verdict as its `decided_by` and a
    watermark's hand-off as that hand-off's actor, so the tree can still say who
    accepted a proposal and who asked for the work. When is not kept: every op
    a fold emits carries the fold's own clock."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws, actor="loop")
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    ins(ws, "p1", list(refs(ws, "p1")[-1]), " Proposed.", actor="loop", plan=pid)
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor=H)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"asked": "build it"}}], actor=H)
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"},
                "payload": {"covers": dict(ws.store.frontier)}}], actor="loop")
    assert ws.store.plans[pid].decided_by == H
    ws.close()
    fold(cd)
    st = Workspace(cd, readonly=True).store
    assert st.plans[pid].status == "accepted" and st.plans[pid].decided_by == H
    verdicts = [o for o in st.ops if o.kind == K.PLAN_ACCEPT]
    handoffs = [o for o in st.ops if o.kind == K.FEATURE_HANDOFF]
    assert [o.actor for o in verdicts] == [H], [(o.actor, o.kind) for o in verdicts]
    assert [o.actor for o in handoffs] == [H], [(o.actor, o.kind) for o in handoffs]


def test_of_two_folds_the_one_covering_more_is_the_state(tmp_path):
    """Clone Y writes more and folds first; clone X folds later without Y's
    session. After the merge, Y's fold (the earlier name) covers more and is
    the state, X's is the one reported, and nothing either clone wrote is lost."""
    import shutil
    x = tmp_path / "x" / ".codoc"
    ws = Workspace(x); seed(ws); ws.close()
    y = tmp_path / "y" / ".codoc"
    shutil.copytree(x, y)
    wy = Workspace(y)
    ws_paragraph(wy, "py", "Only Y wrote this.", after="p1")
    wy.close()
    fold(y)
    fold(x)
    (snap_x,) = L.list_snapshots(x)
    (snap_y,) = L.list_snapshots(y)
    assert snap_x.name > snap_y.name                      # X folded later
    shutil.copy(snap_y, x / "log" / snap_y.name)
    for p in L.list_archived(y):
        shutil.copy(p, x / "log" / "archive" / p.name)
    merged = Workspace(x, readonly=True)
    (problem,) = [p for p in merged.problems if "snapshots" in p.reason]
    assert snap_x.name in problem.reason and snap_y.name not in problem.reason
    assert T(merged.store, "py") == "Only Y wrote this." and T(merged.store, "p1") == "Retries once."


def test_a_fold_never_folds_a_fold(tmp_path):
    """Two folds in a row must not carry the first fold's own ops into the
    second: an archive that grows by a copy of the state per fold is not a
    compaction."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor=H)
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {}}], actor="loop")
    ws.close()
    fold(cd)
    first = _folded(cd)[1]
    fold(cd)
    assert _folded(cd)[1] == first, "the second fold grew"
    assert len(L.list_snapshots(cd)) == 1


def test_drift_survives_a_fold_that_takes_every_op_away(tmp_path):
    """Drift is "the code moved and nobody has looked at the prose since",
    read from three clocks on state: the watermark's `code_at`, the newest
    segment or mark on the prose, and the watermark's `realized_at`. A fold
    can therefore take every binding op and every feature still reads as
    drifted. Forty features, so an accident cannot pass for the property."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    for i in range(40):
        ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": f"h{i}"},
                    "payload": {"kind": HEADING, "level": 1, "after": f"p{i - 1}" if i else START}},
                   {"kind": K.TEXT_INSERT, "target": {"bid": f"h{i}", "path": "text"},
                    "payload": {"after": START, "text": f"Feature {i}"}},
                   {"kind": K.BLOCK_ADD, "target": {"bid": f"p{i}"}, "payload": {"kind": PARAGRAPH, "after": f"h{i}"}},
                   {"kind": K.TEXT_INSERT, "target": {"bid": f"p{i}", "path": "text"},
                    "payload": {"after": START, "text": f"Body {i}."}}], actor="loop")
        ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": f"h{i}"},
                    "payload": {"file": f"m{i}.py", "symbol": f"m{i}.py::f", "fingerprint": "a"}}], actor="loop")
        ws.submit([{"kind": K.BIND_REFRESH, "target": {"fid": f"h{i}"},
                    "payload": {"file": f"m{i}.py", "symbol": f"m{i}.py::f", "fingerprint": "b"}}], actor="loop")
    before = {h.id: phase(ws.store, h.id) for h in ws.store.headings()}
    assert set(before.values()) == {"drifted"}, before
    ws.close()
    fold(cd)
    ws2 = Workspace(cd, readonly=True)
    assert {h.id: phase(ws2.store, h.id) for h in ws2.store.headings()} == before
    # …and the fold really did take the ops: this is the state the old rule
    # could not fold at all.
    assert not [o for o in ws2.store.ops if o.kind.startswith("bind.")
                and o.actor != "import"], "the fold kept the binding ops"
    ws2.close()


def test_two_clones_that_each_folded_do_not_lose_one_clones_work(tmp_path):
    """Two clones that each folded cannot both be the state. The rebuild uses
    the newest snapshot, reports the other, and replays the session files the
    other fold archived, so neither clone's feature is lost."""
    base = tmp_path / "base" / ".codoc"
    ws = Workspace(base)
    seed(ws)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {}}], actor=H)
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {}}], actor="loop")
    ws.close()
    import shutil
    a, b = tmp_path / "a" / ".codoc", tmp_path / "b" / ".codoc"
    for d in (a, b):
        d.parent.mkdir()
        shutil.copytree(base, d)
    for d, bid, title in ((a, "h2", "Alpha"), (b, "h3", "Beta")):
        w = Workspace(d)
        w.submit([{"kind": K.BLOCK_ADD, "target": {"bid": bid}, "payload": {"kind": HEADING, "level": 1, "after": "p1"}},
                  {"kind": K.TEXT_INSERT, "target": {"bid": bid, "path": "text"}, "payload": {"after": START, "text": title}},
                  {"kind": K.FEATURE_HANDOFF, "target": {"fid": bid}, "payload": {}}], actor=H)
        w.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": bid}, "payload": {}}], actor="loop")
        w.close()
        fold(d)
    for src in (b / "log").rglob("*.jsonl"):                 # the git merge: both folds arrive
        dst = a / "log" / src.relative_to(b / "log")
        dst.parent.mkdir(exist_ok=True)
        if not dst.exists():
            shutil.copy(src, dst)
    m = Workspace(a, readonly=True)
    titles = {T(m.store, h.id) for h in m.store.headings()}
    assert {"Alpha", "Beta"} <= titles, (titles, m.problems)
    assert any("another clone" in p.reason for p in m.problems), m.problems
    m.close()


def test_a_fold_does_not_reapply_an_op_the_applier_refused(tmp_path):
    """The fold replays the record like every rebuild: an op recorded refused
    stays refused, so a stranger's refused op in a merged log does not become
    trunk text at the next `codoc snapshot`."""
    cd = tmp_path / ".codoc"
    roles = Roles(table={"human:gh:alice": "suggest", H: "owner"}, default="suggest")
    ws = Workspace(cd, roles=roles)
    seed(ws)
    r = refs(ws, "p1")
    # Straight into the log, as a merged foreign session arrives: no daemon
    # drain to wrap it in a plan of theirs.
    refused = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                          "payload": {"after": list(r[-1]), "text": " Alice was here."}}],
                        actor="human:gh:alice")[0]
    assert refused.conflict == "authority" and "Alice" not in T(ws.store, "p1")
    ws.close()
    fold(cd)
    ws2 = Workspace(cd, roles=roles, readonly=True)
    assert "Alice" not in T(ws2.store, "p1"), T(ws2.store, "p1")


def test_a_hand_off_given_after_a_fold_still_carries_what_the_person_changed(tmp_path):
    """The directive says what the person changed since the last realization.
    A segment carries the op and actor that killed it, and a mark its placer
    and clock, so a hand-off given after a fold still renders the `removed:`
    and Focus lines, although the fold took the ops that wrote them."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    r = refs(ws, "p1")
    ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"},
                "payload": {"from": list(r[8]), "to": list(r[11])}},
               {"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": BOLD, "attrs": {}, "start": list(r[0]), "end": list(r[6]), "on": True}}], actor=H)
    assert T(ws.store, "p1") == "Retries ."
    ws.close()
    fold(cd)
    ws2 = Workspace(cd)
    ws2.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"asked": "drop the retry"}}], actor=H)
    d = directive(ws2.store, "h1")
    assert 'removed: "once"' in d and 'Focus: "Retries"' in d, d
    ws2.close()


def test_snapshot_folds_and_rebuilds_identically(tmp_path):
    """A fold is the state, re-expressed: blocks, bindings, plans with their
    verdicts, and the watermarks (with what the person asked) all survive it."""
    from codoc.calculus import ops as K
    from codoc.calculus.derive import phase
    from codoc.loops.realize import directive
    from tests.helpers import handoff

    ws = Workspace(tmp_path / ".codoc")
    bootstrap(ws, ROWS, Graph(), propose_file=fake_file, propose_org=fake_org, propose_the_brief=lambda *a, **k: {}, concurrency=1)
    fid = ws.store.headings()[0].id
    body = ws.store.feature_body(fid)[-1]
    t = body.text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": body.id, "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " Mine."}}], actor="human:local")
    handoff(ws, fid, actor="human:local", asked="make it retry twice")
    kept = ws.open_plan(actor="loop", kind="edit", target=fid, note="a proposal nobody answered")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": body.id, "path": "text"},
                "payload": {"after": list(ws.store.blocks[body.id].text().last_live_ref()), "text": " Maybe."}}],
              actor="loop", plan=kept)
    gone = ws.open_plan(actor="loop", kind="edit", target=fid, note="answered")
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": gone}, "payload": {}}], actor="human:local")
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": fid}, "payload": {}}], actor="human:local")
    before, before_phase, before_directive = ws.store.state_fingerprint(), phase(ws.store, fid), directive(ws.store, fid)
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    write_snapshot(tmp_path / ".codoc", ws2.store, ws2.clock)
    assert len(L.list_snapshots(tmp_path / ".codoc")) == 1
    assert all("archive" not in str(p) for p in L.list_sessions(tmp_path / ".codoc"))
    # An open proposal is work in flight, so the fold stops before it and the
    # session file holding it stays the log — the verdict still reads its ops.
    assert L.list_sessions(tmp_path / ".codoc"), "the log a verdict still needs was archived"
    ws2.close()
    ws3 = Workspace(tmp_path / ".codoc")
    assert not ws3.problems
    assert ws3.store.state_fingerprint() == before
    assert phase(ws3.store, fid) == before_phase
    assert "make it retry twice" in directive(ws3.store, fid) == before_directive
    assert all(o.conflict is None for o in ws3.store.ops), [o.conflict for o in ws3.store.ops if o.conflict]
