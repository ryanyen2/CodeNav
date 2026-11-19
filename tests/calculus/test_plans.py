"""Plan verdicts: accept, reject and reopen act on a plan's own ops by identity."""
import copy

from codoc.calculus import ops as K
from codoc.calculus.apply import apply, replay_recorded
from codoc.calculus.derive import phase
from codoc.calculus.doc import PARAGRAPH
from codoc.calculus.derive import export_markdown
from codoc.calculus.ids import START
from codoc.calculus.ops import Op
from codoc.calculus.store import Store
from codoc.calculus.text import SUGGEST_DEL
from codoc.index.changes import ChangeSet
from codoc.index.graph import Graph
from codoc.loops.reflect import reflect
from codoc.workspace import Workspace
from codoc.workspace.snapshot import settled_prefix, snapshot_ops, state_at
from tests.helpers import (
    H,
    Clock,
    Mint,
    T,
    chunk,
    heading,
    ins,
    invert,
    paragraph,
    refs,
    replay,
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
    retire = lambda c, s, t, **kw: [{"kind": "delete_node", "feature_id": "h1", "rationale": "gone"}]
    r = reflect(ws, ChangeSet(modified=[chunk("c.py::send", "f2", "t1")]), Graph(), propose=retire)
    acc = ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": r.plans[0]}, "payload": {}}], actor="human:local")[0]
    assert not ws.store.blocks["h1"].live and "c.py::send" not in ws.store.bindings
    ws.submit([dict(b) for b in acc.inverse], actor="human:local")
    assert ws.store.blocks["h1"].live and ws.store.bindings["c.py::send"].feature == "h1"


def test_plan_reject_restores_a_binding_the_plan_only_refreshed():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h1"}, {"symbol": "C", "fingerprint": "f1"}))
    apply(s, m.op(K.PLAN_OPEN, {"pid": "p-1"}, {"kind": "edit", "target": "h1"}, actor="agent:x"))
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h1"}, {"symbol": "C", "fingerprint": "f2"}, actor="agent:x", plan="p-1"))
    apply(s, m.op(K.PLAN_REJECT, {"pid": "p-1"}, {}))
    assert s.bindings["C"].fingerprint == "f1"


def test_a_heading_deleted_under_a_paragraph_plan_and_restored_keeps_the_plan_decidable(tmp_path):
    ws = Workspace(tmp_path / ".codoc"); seed(ws, actor="loop")
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    ins(ws, "p1", list(refs(ws, "p1")[-1]), " Proposed.", actor="loop", plan=pid)
    from codoc.calculus.derive import offered_plans, projection
    assert [p.id for p in offered_plans(ws.store)] == [pid]
    d = ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor=H)[0]
    assert offered_plans(ws.store) == []                      # not offered while its words are dead
    projection(ws.store)                                   # renders without error
    ws.submit([dict(b) for b in d.inverse], actor=H)
    assert [p.id for p in offered_plans(ws.store)] == [pid]   # back with the heading
    assert phase(ws.store, "h1") == "proposed"
    ws.close()


# ── a verdict records ordered steps; a reopen undoes them newest first ───────

def _proposal(*kinds):
    """Paragraph "Base." and plan P by an agent, then, in order, for each
    name: "insert" " Foo", "delete" it, or "restore" it."""
    m, s, ag = Mint(), Store(), Mint("a", "agent:x")
    heading(m, s, "h1", "Feat")
    paragraph(m, s, "p1", "Base.", after="h1")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "edit", "target": "h1"}))
    out: list[Op] = []
    for kind in kinds:
        if kind == "insert":
            op = ag.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"},
                       {"after": list(s.blocks["p1"].text().last_live_ref()), "text": " Foo"}, plan="P")
        elif kind == "delete":
            op = ag.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"segments": [[out[0].id, 0, 4]]}, plan="P")
        else:
            last_delete = next(o for o in reversed(out) if o.kind == K.TEXT_DELETE)
            op = ag.op(K.TEXT_RESTORE, {"bid": "p1", "path": "text"},
                       {"segments": [[out[0].id, 0, 4]], "by": last_delete.id}, plan="P")
        out.append(apply(s, op))
        assert out[-1].conflict is None, out[-1].conflict
    return m, s, out


def _verdict_then_reopen(m, s, kind, payload) -> str:
    """Apply the verdict, then its reopen: the reopen gives back exactly the
    state before the verdict. Returns the paragraph as the verdict left it."""
    before = copy.deepcopy(s.state_fingerprint())
    v = apply(s, m.op(kind, {"pid": "P"}, payload))
    assert v.conflict is None
    after = T(s, "p1")
    (r,) = invert(s, m, v)
    assert r.conflict is None and s.state_fingerprint() == before
    assert replay(s).state_fingerprint() == s.state_fingerprint()
    return after


def test_accepting_only_the_insert_of_words_the_plan_took_back_and_gave_back_keeps_them():
    """Undoing the restore puts the words back to dead by the delete it undid,
    so undoing the delete then brings them back."""
    m, s, (a, *_) = _proposal("insert", "delete", "restore")
    assert _verdict_then_reopen(m, s, K.PLAN_ACCEPT, {"subset": [a.id]}) == "Base. Foo"
    m, s, (a, *_) = _proposal("insert", "delete", "restore", "delete")
    assert T(s, "p1") == "Base."
    assert _verdict_then_reopen(m, s, K.PLAN_ACCEPT, {"subset": [a.id]}) == "Base. Foo"
    m, s, _ = _proposal("insert", "delete", "restore")
    assert _verdict_then_reopen(m, s, K.PLAN_REJECT, {}) == "Base."


def test_reopening_a_reject_of_two_kind_changes_ends_at_the_second():
    m, s, ag = Mint(), Store(), Mint("a", "agent:x")
    heading(m, s, "h1", "Feat")
    paragraph(m, s, "p1", "Base.", after="h1")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "edit", "target": "h1"}))
    for kind in ("bullet", "numbered"):
        assert apply(s, ag.op(K.BLOCK_SET, {"bid": "p1", "path": "kind"}, {"value": kind}, plan="P")).conflict is None
    _verdict_then_reopen(m, s, K.PLAN_REJECT, {})
    assert s.blocks["p1"].kind == "numbered"


def test_reopening_a_reject_of_a_planned_unset_leaves_the_field_absent():
    m, s, ag = Mint(), Store(), Mint("a", "agent:x")
    heading(m, s, "h1", "Feat")
    paragraph(m, s, "p1", "Base.", after="h1")
    apply(s, m.op(K.BLOCK_SET, {"bid": "p1", "path": "x"}, {"value": 1}))
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "edit", "target": "h1"}))
    assert apply(s, ag.op(K.BLOCK_SET, {"bid": "p1", "path": "x"}, {"unset": True}, plan="P")).conflict is None
    rej = apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    assert s.blocks["p1"].content["x"] == 1
    invert(s, m, rej)
    assert "x" not in s.blocks["p1"].content and "x" not in replay(s).blocks["p1"].content


def test_a_reopen_after_the_paragraph_was_deleted_undoes_the_verdict_inside_it():
    """The verdict's steps are undone in the dead paragraph, so restoring the
    paragraph afterwards shows the proposal as it was before the verdict."""
    m, s, (a, _) = _proposal("insert", "delete")
    x = apply(s, Mint("a2", "agent:x").op(K.TEXT_INSERT, {"bid": "p1", "path": "text"},
                                          {"after": [a.id, 3], "text": " Bar"}, plan="P"))
    assert T(s, "p1") == "Base. Bar"
    v = apply(s, m.op(K.PLAN_ACCEPT, {"pid": "P"}, {"subset": [a.id]}))
    assert v.conflict is None and T(s, "p1") == "Base. Foo" and x.id in v.payload["rejected"]
    d = apply(s, m.op(K.BLOCK_DELETE, {"bid": "p1"}, {}))
    (r,) = invert(s, m, v)
    assert r.conflict is None and s.plans["P"].status == "open"
    assert replay(s).state_fingerprint() == s.state_fingerprint()
    invert(s, m, d)
    assert T(s, "p1") == "Base. Bar"


def test_a_verdict_a_fold_archived_is_not_reopened():
    """A redo judges the plan's ops, which the fold archived with the verdict,
    so the undo of a folded verdict is a conflict that changes nothing."""
    m, s, (a, _) = _proposal("insert", "delete")
    v = apply(s, m.op(K.PLAN_ACCEPT, {"pid": "P"}, {"subset": [a.id]}))
    assert T(s, "p1") == "Base. Foo"
    folded = Store()
    for d in snapshot_ops(s, "snap", Clock()):
        replay_recorded(folded, Op.from_json(copy.deepcopy(d)))
    assert folded.state_fingerprint() == s.state_fingerprint() and a.id not in folded.by_id
    before = copy.deepcopy(folded.state_fingerprint())
    (r,) = invert(folded, Mint("u", "human:a"), v)
    assert r.conflict == "verdict_folded" and folded.state_fingerprint() == before
    assert replay(folded).state_fingerprint() == folded.state_fingerprint()


def test_a_folded_accept_of_a_delete_plan_does_not_kill_its_restored_heading():
    """The fold's verdict restores the plan's status; the blocks it restores
    already hold what the verdict did, and a heading the person brought back
    since stays."""
    m, s, ag = Mint(), Store(), Mint("a", "agent:x")
    heading(m, s, "h1", "A")
    heading(m, s, "h2", "B", after="h1")
    d = apply(s, m.op(K.BLOCK_DELETE, {"bid": "h2"}, {}))
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "D"}, {"kind": "delete", "target": "h1"}))
    apply(s, m.op(K.PLAN_ACCEPT, {"pid": "D"}, {}))
    invert(s, m, d)
    assert not s.blocks["h1"].live
    apply(s, m.op(K.BLOCK_RESTORE, {}, {"ids": ["h1"]}))
    assert s.blocks["h1"].live and s.plans["D"].status == "accepted"
    folded = Store()
    for o in snapshot_ops(s, "snap", Clock()):
        replay_recorded(folded, Op.from_json(copy.deepcopy(o)))
    assert folded.state_fingerprint() == s.state_fingerprint()


# ── a verdict never takes its effects from a payload ──────────────────────────

def test_a_withdraw_carrying_forged_steps_changes_nothing():
    """A machine may withdraw its own empty plan. Steps it sends are not read:
    the applier computes what a verdict does from the plan, and records that."""
    from codoc.workspace.roles import Roles
    s, m, ag = Store(Roles.from_config({}).policy), Mint(), Mint("a", "agent:x")
    heading(m, s, "h1", "Top")
    words = paragraph(m, s, "p1", "owner words", after="h1")
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h1"}, {"symbol": "a"}))
    assert apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "edit"})).conflict is None
    before = copy.deepcopy(s.state_fingerprint())
    forged = [{"do": "kill", "bid": "p1", "path": "text", "segments": [[words.id, 0, 11]]},
              {"do": "kill_block", "bid": "h1"},
              {"do": "bind", "symbol": "a", "from": None, "to": None}]
    rj = apply(s, ag.op(K.PLAN_REJECT, {"pid": "P"}, {"steps": forged, "rejected": [words.id]}))
    assert rj.conflict is None and rj.payload["steps"] == [] and rj.payload["rejected"] == []
    after = s.state_fingerprint()
    assert after["blocks"] == before["blocks"] and after["bindings"] == before["bindings"]
    assert replay(s).state_fingerprint() == after
    # A reopen's own payload names no effects either: it inverts what the store recorded.
    m2, s2, (a,) = _proposal("insert")
    v = apply(s2, m2.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    r = apply(s2, m2.op(K.PLAN_REOPEN, {"pid": "P"}, {"by": v.id, "steps": forged[:1], "from": "accepted"}))
    assert r.conflict is None and T(s2, "p1") == "Base. Foo" and s2.plans["P"].status == "open"
    (redo,) = r.inverse
    assert redo["kind"] == K.PLAN_REJECT


def test_a_redo_of_a_reject_undoes_what_the_plan_took_back_since_the_reopen():
    m, s, ag = Mint(), Store(), Mint("a", "agent:x")
    heading(m, s, "h1", "Top")
    x = paragraph(m, s, "p1", "x", after="h1")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "edit"}))
    i = apply(s, ag.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [x.id, 0], "text": "abc"}, plan="P"))
    (r,) = invert(s, m, apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {})))
    assert T(s, "p1") == "xabc"
    apply(s, ag.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"segments": [[i.id, 1, 1]]}, plan="P"))
    (redo,) = invert(s, m, r)
    assert redo.conflict is None and T(s, "p1") == "x"
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def test_a_redo_of_a_delete_accept_takes_a_paragraph_written_after_the_reopen():
    m, s, ag = Mint(), Store(), Mint("a", "agent:x")
    heading(m, s, "h1", "A"); paragraph(m, s, "p1", "a body", after="h1")
    heading(m, s, "h2", "B", after="p1"); paragraph(m, s, "p2", "b body", after="h2")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "D"}, {"kind": "delete", "target": "h2"}))
    (r,) = invert(s, m, apply(s, m.op(K.PLAN_ACCEPT, {"pid": "D"}, {})))
    paragraph(m, s, "p3", "new words under B", after="p2")
    (redo,) = invert(s, m, r)
    assert redo.conflict is None and [b.id for b in s.ordered()] == ["h1", "p1"]
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def test_a_reject_after_a_reopen_of_a_reject_puts_the_move_back():
    """The reopen's own re-rank is the plan's, not someone else's move."""
    m, s, ag = Mint(), Store(), Mint("a", "agent:x")
    heading(m, s, "h1", "Top")
    paragraph(m, s, "a", "a", after="h1"); paragraph(m, s, "b", "b", after="a"); paragraph(m, s, "c", "c", after="b")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "M"}, {"kind": "move"}))
    apply(s, ag.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "c"}, plan="M"))
    invert(s, m, apply(s, m.op(K.PLAN_REJECT, {"pid": "M"}, {})))
    assert [b.id for b in s.ordered()] == ["h1", "b", "c", "a"]
    assert apply(s, m.op(K.PLAN_REJECT, {"pid": "M"}, {})).conflict is None
    assert [b.id for b in s.ordered()] == ["h1", "a", "b", "c"]


def test_a_move_made_and_undone_since_the_verdict_does_not_stop_the_reopen():
    """Someone drags the block and takes it back with Cmd-Z: nothing moved, so
    the reopen puts the plan's move back and the accept keeps it."""
    m, s, ag, b = Mint(), Store(), Mint("a", "agent:x"), Mint("b", "human:b")
    heading(m, s, "h1", "Top")
    paragraph(m, s, "a", "a", after="h1"); paragraph(m, s, "b", "b", after="a"); paragraph(m, s, "c", "c", after="b")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "M"}, {"kind": "move"}))
    apply(s, ag.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "c"}, plan="M"))
    rej = apply(s, m.op(K.PLAN_REJECT, {"pid": "M"}, {}))
    invert(s, b, apply(s, b.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "b"})))
    assert [x.id for x in s.ordered()] == ["h1", "a", "b", "c"]
    invert(s, m, rej)
    assert apply(s, m.op(K.PLAN_ACCEPT, {"pid": "M"}, {})).conflict is None
    assert [x.id for x in s.ordered()] == ["h1", "b", "c", "a"]


def test_two_rejected_plans_that_moved_one_block_leave_it_where_neither_put_it():
    m, s, ag = Mint(), Store(), Mint("a", "agent:x")
    heading(m, s, "h1", "Top")
    paragraph(m, s, "a", "a", after="h1"); paragraph(m, s, "b", "b", after="a"); paragraph(m, s, "c", "c", after="b")
    for pid, after in (("P", "c"), ("Q", "b")):
        apply(s, ag.op(K.PLAN_OPEN, {"pid": pid}, {"kind": "move"}))
        apply(s, ag.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": after}, plan=pid))
    for pid in ("P", "Q"):
        assert apply(s, m.op(K.PLAN_REJECT, {"pid": pid}, {})).conflict is None
    assert [x.id for x in s.ordered()] == ["h1", "a", "b", "c"]
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def test_a_plans_restore_form_move_is_put_back_by_a_reject():
    """Whatever op a plan's restore row names as having ranked the block, the
    restore is the op that did, so its reject puts the block back."""
    for row in (["c", "0", 0], ["c", "0", 0, "g:1"]):
        m, s, ag = Mint(), Store(), Mint("a", "agent:x")
        heading(m, s, "h1", "Top")
        paragraph(m, s, "b", "b", after="h1"); paragraph(m, s, "c", "c", after="b")
        apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "move"}))
        apply(s, ag.op(K.BLOCK_MOVE, {}, {"restore": [row]}, plan="P"))
        assert [x.id for x in s.ordered()][0] == "c"
        apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {}))
        assert [x.id for x in s.ordered()] == ["h1", "b", "c"], row
        assert replay(s).state_fingerprint() == s.state_fingerprint()


def test_undoing_a_merge_after_a_reject_brings_the_block_back_where_the_reject_put_it():
    m, s, ag = Mint(), Store(), Mint("a", "agent:x")
    heading(m, s, "h1", "Top")
    paragraph(m, s, "a", "a", after="h1"); paragraph(m, s, "b", "b", after="a"); paragraph(m, s, "c", "c", after="b")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "move"}))
    apply(s, ag.op(K.BLOCK_MOVE, {"bid": "c"}, {"after": "a"}, plan="P"))
    merged = apply(s, m.op(K.BLOCK_MERGE, {"into": "a", "bid": "c"}, {}))
    apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    invert(s, m, merged)
    assert [x.id for x in s.ordered()] == ["h1", "a", "b", "c"]
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def _abc():
    m, s, ag, b = Mint(), Store(), Mint("a", "agent:x"), Mint("b", "human:b")
    heading(m, s, "h1", "Top")
    paragraph(m, s, "a", "a", after="h1"); paragraph(m, s, "b", "b", after="a"); paragraph(m, s, "c", "c", after="b")
    return m, s, ag, b


def test_a_plans_two_moves_are_undone_when_its_first_moves_rank_was_handed_out_again():
    """A block added in the gap P's first move used gets that move's rank, and
    undoing P's second move cannot give it back. P's first move is still the
    one that ranked the block, so the reject undoes it too."""
    m, s, ag, b = _abc()
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "move"}))
    apply(s, ag.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "b"}, plan="P"))
    apply(s, ag.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "c"}, plan="P"))
    paragraph(b, s, "y", "y", after="b")
    apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    assert [x.id for x in s.ordered()] == ["h1", "a", "b", "y", "c"]
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def test_a_rejected_plans_old_move_to_the_same_place_does_not_undo_a_persons_move():
    """Q moved a after b and was rejected. The person then moves a after b
    themselves, to the same rank. Rejecting P, which moved a after c, gives a
    back to the person's place."""
    m, s, ag, b = _abc()
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "Q"}, {"kind": "move"}))
    apply(s, ag.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "b"}, plan="Q"))
    apply(s, m.op(K.PLAN_REJECT, {"pid": "Q"}, {}))
    apply(s, b.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "b"}))
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "move"}))
    apply(s, ag.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "c"}, plan="P"))
    apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    assert [x.id for x in s.ordered()] == ["h1", "b", "a", "c"]


def test_a_reopen_redoes_both_moves_when_the_first_moves_rank_was_handed_out_again():
    m, s, ag, b = _abc()
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "move"}))
    apply(s, ag.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "b"}, plan="P"))
    apply(s, ag.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "c"}, plan="P"))
    rej = apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    paragraph(b, s, "y", "y", after="b")
    (ro,) = invert(s, m, rej)
    assert ro.conflict is None and [x.id for x in s.ordered()] == ["h1", "b", "y", "c", "a"]
    assert apply(s, m.op(K.PLAN_ACCEPT, {"pid": "P"}, {})).conflict is None
    assert [x.id for x in s.ordered()] == ["h1", "b", "y", "c", "a"]
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def test_a_persons_move_to_where_the_plan_put_the_block_stands_on_reject():
    """Merged from two clones: P moved a after b, and the person did too. The
    person's move gave the same rank, and it still stands."""
    m, s, ag, b = _abc()
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "move"}))
    apply(s, ag.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "b"}, plan="P"))
    apply(s, b.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "b"}))
    apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    assert [x.id for x in s.ordered()] == ["h1", "b", "a", "c"]


def test_undoing_a_move_back_to_a_rejected_plans_place_goes_to_where_the_block_was_before_it():
    """P moved a after c, the person moved it after b, P was rejected and
    the person's move stood. The person's undo does not bring back the place P
    proposed and the owner turned down."""
    m, s, ag, b = _abc()
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "move"}))
    apply(s, ag.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "c"}, plan="P"))
    mv = apply(s, b.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "b"}))
    apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    (un,) = invert(s, b, mv)
    assert un.conflict is None and [x.id for x in s.ordered()] == ["h1", "a", "b", "c"]
    (re,) = invert(s, b, un)
    assert re.conflict is None and [x.id for x in s.ordered()] == ["h1", "b", "a", "c"]
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def test_a_rejected_kind_change_leaves_a_paragraph_with_no_level():
    """P makes a paragraph a bullet, Q sets its level; rejecting both gives the
    paragraph back with level 0, the same in a rebuild."""
    m, s, ag = Mint(), Store(), Mint("a", "agent:x")
    heading(m, s, "h1", "Top"); paragraph(m, s, "p", "words", after="h1")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "edit"}))
    apply(s, ag.op(K.BLOCK_SET, {"bid": "p", "path": "kind"}, {"value": "bullet"}, plan="P"))
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "Q"}, {"kind": "edit"}))
    apply(s, ag.op(K.BLOCK_SET, {"bid": "p", "path": "level"}, {"value": 3}, plan="Q"))
    for pid in ("P", "Q"):
        apply(s, m.op(K.PLAN_REJECT, {"pid": pid}, {}))
    assert (s.blocks["p"].kind, s.blocks["p"].level) == (PARAGRAPH, 0)
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def test_a_reopen_of_a_verdict_that_is_not_the_plans_current_one_is_refused():
    m, s, ag, other = Mint(), Store(), Mint("a", "agent:x"), Mint("b", "human:b")
    heading(m, s, "h1", "Top")
    x = paragraph(m, s, "p1", "x", after="h1")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "edit"}))
    apply(s, ag.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [x.id, 0], "text": " plan"}, plan="P"))
    v1 = apply(s, m.op(K.PLAN_ACCEPT, {"pid": "P"}, {}))
    invert(s, other, v1)
    apply(s, other.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    before = copy.deepcopy(s.state_fingerprint())
    (stale,) = invert(s, m, v1)                 # the first undo, sent late
    assert stale.conflict == "not_current_verdict" and s.state_fingerprint() == before


def _marks(s):
    return sorted((b.id, m.kind, m.attrs.get("mark", "")) for b in s.ordered() for m in b.text().marks)


def test_a_reopen_finds_the_marks_a_verdict_placed_after_a_split_cut_them():
    m, s, ag = Mint(), Store(), Mint("a", "agent:x")
    heading(m, s, "h1", "Top")
    x = paragraph(m, s, "p1", "alpha beta", after="h1")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "edit"}))
    apply(s, ag.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                   {"kind": "bold", "attrs": {}, "start": [x.id, 0], "end": [x.id, 9], "on": True}, plan="P"))
    acc = apply(s, m.op(K.PLAN_ACCEPT, {"pid": "P"}, {}))
    apply(s, m.op(K.BLOCK_SPLIT, {"bid": "p1", "path": "text"}, {"at": [x.id, 5], "new": "p2"}))
    assert _marks(s) == [("p1", "bold", ""), ("p2", "bold", "")]
    (ro,) = invert(s, m, acc)
    assert ro.conflict is None and _marks(s) == [("p1", "suggest_mark", "bold"), ("p2", "suggest_mark", "bold")]
    assert apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {})).conflict is None and _marks(s) == []
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def test_a_reopen_puts_back_a_proposed_cut_across_a_split_and_accepting_it_cuts():
    m, s, ag = Mint(), Store(), Mint("a", "agent:x")
    heading(m, s, "h1", "Top")
    x = paragraph(m, s, "p1", "alpha beta", after="h1")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "edit"}))
    apply(s, ag.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                   {"kind": SUGGEST_DEL, "attrs": {}, "start": [x.id, 0], "end": [x.id, 9], "on": True}, plan="P"))
    rj = apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    apply(s, m.op(K.BLOCK_SPLIT, {"bid": "p1", "path": "text"}, {"at": [x.id, 5], "new": "p2"}))
    (ro,) = invert(s, m, rj)
    assert ro.conflict is None and _marks(s) == [("p1", SUGGEST_DEL, ""), ("p2", SUGGEST_DEL, "")]
    assert apply(s, m.op(K.PLAN_ACCEPT, {"pid": "P"}, {})).conflict is None
    assert [T(s, b) for b in ("p1", "p2")] == ["", ""] and _marks(s) == []
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def test_undoing_a_proposed_cut_after_a_split_removes_both_pieces():
    m, s, ag = Mint(), Store(), Mint("a", "agent:x")
    heading(m, s, "h1", "Top")
    x = paragraph(m, s, "p1", "alpha beta", after="h1")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "edit"}))
    cut = apply(s, ag.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                         {"kind": SUGGEST_DEL, "attrs": {}, "start": [x.id, 0], "end": [x.id, 9], "on": True}, plan="P"))
    apply(s, m.op(K.BLOCK_SPLIT, {"bid": "p1", "path": "text"}, {"at": [x.id, 5], "new": "p2"}))
    (un,) = invert(s, ag, cut)
    assert un.conflict is None and _marks(s) == []
    redo = invert(s, ag, un)                    # one piece per paragraph
    assert [r.conflict for r in redo] == [None, None]
    assert _marks(s) == [("p1", SUGGEST_DEL, ""), ("p2", SUGGEST_DEL, "")]
    assert replay(s).state_fingerprint() == s.state_fingerprint()


# ── live and fold-then-replay agree ───────────────────────────────────────────

def _folded(s: Store) -> tuple[Store, int]:
    """The store a rebuild makes after `codoc snapshot`: the settled prefix
    folded, then the rest replayed as recorded."""
    cut = settled_prefix(s)
    folded = Store()
    for d in snapshot_ops(state_at(s, cut), "snap", Clock()):
        assert replay_recorded(folded, Op.from_json(d)).conflict is None
    for o in s.ops[cut:]:
        replay_recorded(folded, Op.from_json(copy.deepcopy(o.to_json())))
    return folded, cut


def _late_open_plan(m: Mint, s: Store, ag: Mint) -> None:
    """A second feature with a plan still open, so the cut falls after it."""
    heading(m, s, "h2", "B", after="p1")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "Q"}, {"kind": "edit", "target": "h2"}))
    apply(s, ag.op(K.TEXT_INSERT, {"bid": "h2", "path": "text"}, {"after": START, "text": "q"}, plan="Q"))


def test_a_fold_keeps_a_plan_answered_after_the_cut():
    """The plan was open where the cut falls and decided after it. Folding
    its ops would state it decided before its verdict."""
    m, s, ag = Mint(), Store(), Mint("a", "agent:x")
    heading(m, s, "h1", "A")
    x = paragraph(m, s, "p1", "x", after="h1")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "edit", "target": "h1"}))
    apply(s, ag.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [x.id, 0], "text": " plan"}, plan="P"))
    _late_open_plan(m, s, ag)
    apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    folded, cut = _folded(s)
    assert cut > 0 and folded.state_fingerprint() == s.state_fingerprint()


def test_a_plans_own_restore_after_a_reopen_replays_the_same_after_a_fold():
    """Accepted, then the person's undo reopens it and the plan gives back a
    word it took back, then it is accepted again. The fold keeps the plan's
    ops, and whether the restore takes back the plan's own delete is asked of
    the plan's op list, which a fold keeps, not of an op it archived."""
    m, s, ag = Mint(), Store(), Mint("a", "agent:x")
    heading(m, s, "h1", "Top")
    x = paragraph(m, s, "p1", "x", after="h1")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "edit"}))
    i = apply(s, ag.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [x.id, 0], "text": "abc"}, plan="P"))
    d = apply(s, ag.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"segments": [[i.id, 1, 1]]}, plan="P"))
    v = apply(s, m.op(K.PLAN_ACCEPT, {"pid": "P"}, {}))
    _late_open_plan(m, s, ag)
    invert(s, m, v)
    r = apply(s, ag.op(K.TEXT_RESTORE, {"bid": "p1", "path": "text"}, {"segments": [[i.id, 1, 1]], "by": d.id}, plan="P"))
    assert r.conflict is None and T(s, "p1") == "xabc"
    apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    assert T(s, "p1") == "x"
    folded, cut = _folded(s)
    assert cut > 0 and folded.state_fingerprint() == s.state_fingerprint()


def test_a_redo_also_decides_what_the_plan_gained_after_the_reopen():
    """Words the plan proposed after the reopen are not in the steps being
    redone; a redo of a reject kills them with the rest."""
    m, s, (a,) = _proposal("insert")
    v = apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    (r,) = invert(s, m, v)
    later = apply(s, Mint("a2", "agent:x").op(K.TEXT_INSERT, {"bid": "p1", "path": "text"},
                                              {"after": [a.id, 3], "text": " Bar"}, plan="P"))
    assert T(s, "p1") == "Base. Foo Bar"
    (again,) = invert(s, m, r)
    assert again.conflict is None and T(s, "p1") == "Base." and later.id in again.payload["rejected"]
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def test_a_word_typed_inside_a_proposed_strike_is_not_drawn_struck(tmp_path):
    """The agent proposes cutting "once"; the person types "X" inside it
    ("onXce"). Accepting kills exactly the characters the strike listed, so
    the strike drawn over the page leaves "X" out."""
    from codoc.calculus.derive import plan_claims
    ws = Workspace(tmp_path / ".codoc")
    seed(ws, actor=H)
    t = ws.store.blocks["p1"].text()
    pid = ws.open_plan(actor="agent:claude-code", kind="edit", target="p1")
    once = [list(t.ref_at(i)) for i in range(8, 12)]            # "Retries |once|."
    ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": SUGGEST_DEL, "attrs": {}, "start": once[0], "end": once[-1], "on": True}}],
              actor="agent:claude-code", plan=pid)
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": once[1], "text": "X"}}], actor=H)
    assert T(ws.store, "p1") == "Retries onXce."
    cuts = [(c.start, c.end, c.text) for c in plan_claims(ws.store, "h1") if c.stage == "removed"]
    assert cuts == [(8, 10, "on"), (11, 13, "ce")]
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor=H)
    assert T(ws.store, "p1") == "Retries X."


def test_a_plan_whose_target_died_does_not_leave_the_feature_its_words_are_in_proposed(tmp_path):
    """A plan whose target is dead is not offered, so no surface can decide
    it; the feature its words landed in is not waiting on a verdict."""
    from codoc.calculus.derive import offered_plans
    ws = Workspace(tmp_path / ".codoc")
    seed(ws, actor="loop")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "h2"}, "payload": {"kind": "heading", "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "h2", "path": "text"}, "payload": {"after": START, "text": "B"}}],
              actor="loop")
    pid = ws.open_plan(actor="agent:claude-code", target="h2")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(ws.store.blocks["p1"].text().last_live_ref()), "text": " Maybe."}}],
              actor="agent:claude-code", plan=pid)
    assert phase(ws.store, "h1") == "proposed"
    ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h2"}, "payload": {}}], actor=H)
    assert offered_plans(ws.store) == [] and phase(ws.store, "h1") == "synced"


def test_accepting_a_build_plans_strike_without_a_hand_off_leaves_it_the_accepters_edit(tmp_path):
    """Accepting a plan that asks for code adopts what it proposed, strikes
    included, as the directive does once a hand-off carries them; until one
    does, the feature is the accepter's unsent edit."""
    from codoc.calculus.derive import editors
    ws = Workspace(tmp_path / ".codoc")
    seed(ws, actor=H)
    agent = "agent:claude-code"
    pid = "p-build"
    ws.submit([{"kind": K.PLAN_OPEN, "target": {"pid": pid}, "payload": {"kind": "edit", "target": "p1", "build": True}}],
              actor=agent, batch=pid)
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": SUGGEST_DEL, "attrs": {}, "start": list(t.ref_at(8)), "end": list(t.ref_at(11)), "on": True}}],
              actor=agent, plan=pid)
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor=H)
    assert T(ws.store, "p1") == "Retries ." and editors(ws.store, "h1") == [H]
