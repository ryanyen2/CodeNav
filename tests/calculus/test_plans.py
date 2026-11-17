"""Plan verdicts: accept, reject and reopen act on a plan's own ops by identity."""
import copy

from codoc.calculus import ops as K
from codoc.calculus.apply import apply, replay_recorded
from codoc.calculus.derive import phase
from codoc.calculus.doc import PARAGRAPH
from codoc.calculus.export import export_markdown
from codoc.calculus.ids import START
from codoc.calculus.ops import Op
from codoc.calculus.store import Store
from codoc.index.changes import ChangeSet
from codoc.index.graph import Graph
from codoc.loops.reflect import reflect
from codoc.workspace import Workspace
from codoc.workspace.snapshot import snapshot_ops
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


def test_a_reopen_and_its_redo_need_no_op_a_fold_archived():
    """Reopen and redo read the verdict's steps, not the plan's ops, so the
    folded store and the live one agree after both."""
    m, s, (a, _) = _proposal("insert", "delete")
    v = apply(s, m.op(K.PLAN_ACCEPT, {"pid": "P"}, {"subset": [a.id]}))
    assert T(s, "p1") == "Base. Foo"
    folded = Store()
    for d in snapshot_ops(s, "snap", Clock()):
        replay_recorded(folded, Op.from_json(copy.deepcopy(d)))
    assert folded.state_fingerprint() == s.state_fingerprint() and a.id not in folded.by_id
    for st in (s, folded):
        u = Mint("u", "human:a")
        (r,) = invert(st, u, v)
        assert r.conflict is None and T(st, "p1") == "Base."
        (again,) = invert(st, u, r)
        assert again.conflict is None and T(st, "p1") == "Base. Foo"
    assert folded.state_fingerprint() == s.state_fingerprint()
    assert replay(folded).state_fingerprint() == folded.state_fingerprint()


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
