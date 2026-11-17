"""Hand-off, the queue, settling, and the directive."""
from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.derive import phase
from codoc.calculus.ops import covered
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD
from codoc.index.changes import ChangeSet, ChunkRef
from codoc.index.graph import Graph
from codoc.loops.realize import (
    directive,
    handoff,
    queued,
    render_queue,
    settle,
    write_queue,
)
from codoc.loops.reflect import reflect
from codoc.workspace import Workspace
from tests.helpers import H, Mint, chunk, heading, paragraph, seed, seed_loop


def test_handoff_queues_directive_and_reflect_under_it_realizes(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " Now retries **three times**."}}], actor="human:local")
    # The bold is a Focus line: mark it as the person.
    t = ws.store.blocks["p1"].text()
    start = t.ref_at(t.live_text().index("three")); end = t.ref_at(t.live_text().index("times") + 4)
    ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": "bold", "attrs": {}, "start": list(start), "end": list(end), "on": True}}], actor="human:local")
    assert phase(ws.store, "h1") == "edited"
    ho = handoff(ws, "h1", actor="human:local", asked="make the client retry three times")
    assert phase(ws.store, "h1") == "queued" and queued(ws.store) == ["h1"]
    md = render_queue(ws.store, root_dir="/repo")
    assert f"⟨d:{ho.id}⟩" in md and "Focus:" in md and "Author asked: make the client retry three times" in md
    assert write_queue(tmp_path / ".codoc", ws.store) is not None
    # The agent implements; the reflect pass cites the hand-off via caused_by.
    cs = ChangeSet(modified=[ChunkRef("c.py", "c.py::send", "f9", "t1")])
    r = reflect(ws, cs, Graph(), propose=lambda *a, **k: [], under_epoch=True)
    assert r.caused == {"h1": ho.id}
    assert any(o.caused_by == ho.id for o in r.ops if o.kind == K.BIND_REFRESH)
    done = settle(ws, r, sha="abc123")
    assert len(done) == 1 and done[0].caused_by == ho.id
    assert phase(ws.store, "h1") == "synced"
    assert queued(ws.store) == [] and write_queue(tmp_path / ".codoc", ws.store) is None


def test_withdraw_restores_the_previous_frontier(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " More."}}], actor="human:local")
    handoff(ws, "h1", actor="human:local")
    assert phase(ws.store, "h1") == "queued"
    # Taking the hand-off back is the recorded inverse, submitted as the person's
    # own op — the same gesture the editor sends.
    last = ws.store.by_id[ws.store.watermarks("h1").handoffs[-1]]
    assert ws.submit([dict(b) for b in last.inverse], actor="human:local")[0].conflict is None
    assert phase(ws.store, "h1") == "edited"


def test_deleted_bound_feature_stays_queued_as_phantom(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor="human:local")
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"},
                "payload": {"covers": dict(ws.store.frontier), "delete_code": True}}], actor="human:local")
    assert queued(ws.store) == ["h1"]
    md = render_queue(ws.store)
    assert "REMOVE FEATURE" in md and "Delete the code" in md


def test_a_hand_edit_never_settles_a_request_but_a_closed_epoch_does(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " Retry five times."}}], actor="human:local")
    handoff(ws, "h1", actor="human:local", asked="retry five times")
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f2", "t1")]), Graph(), propose=lambda *a, **k: [])
    assert r.caused == {} and settle(ws, r, sha="typo") == []
    assert phase(ws.store, "h1") == "queued"
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f3", "t1")]), Graph(), propose=lambda *a, **k: [],
                under_epoch=True)
    assert len(settle(ws, r, sha="built")) == 1
    w = ws.store.watermarks("h1")
    assert covered(w.handoff, w.realized) and phase(ws.store, "h1") == "synced"


def test_two_human_bolds_are_two_focus_items():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    ins = paragraph(m, s, "p1", "Retries once and logs.", after="h1")
    for lo, hi in ((8, 11), (17, 20)):
        apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, lo], "end": [ins.id, hi], "on": True}))
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 17], "end": [ins.id, 20], "on": False}))
    apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {}))
    assert 'Focus: "once"' in directive(s, "h1") and '"logs"' not in directive(s, "h1")   # an un-bold is not a focus


def test_request_built_while_the_person_kept_typing_settles(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    t = ws.store.blocks["p1"].text()
    a = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Do A."}}], actor="human:local")[0]
    handoff(ws, "h1", actor="human:local", asked="do A")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": [a.id, 5], "text": " And B later."}}], actor="human:local")
    r = reflect(ws, ChangeSet(modified=[chunk("c.py", "c.py::send", "f2", "t1")]), Graph(), propose=lambda *a, **k: [], under_epoch=True)
    done = settle(ws, r, sha="built")
    w = ws.store.watermarks("h1")
    assert len(done) == 1 and covered(w.handoff, w.realized) and phase(ws.store, "h1") == "edited"


def test_a_deleted_bound_feature_with_delete_code_can_be_settled(tmp_path):
    """A deleted bound feature handed off with delete_code settles when the
    pass under the agent's epoch sees every symbol it owned removed, although
    the delete already detached the bindings and the pass writes no `bind.*`
    op for it."""
    from codoc.index.changes import ChangeSet, ChunkRef
    from codoc.index.graph import Graph
    from codoc.loops import realize as R
    from codoc.loops.reflect import reflect
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor=H)
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"delete_code": True}}], actor=H)
    assert R.queued(ws.store) == ["h1"]
    # the agent deletes send(); the epoch closes; the reflect pass runs under it
    cs = ChangeSet(removed=[ChunkRef("c.py", "c.py::send", "f1", "t1")])
    res = reflect(ws, cs, Graph(), propose=lambda *a, **k: [], under_epoch=True)
    R.settle(ws, res, sha="abc", files=["c.py"])
    assert R.queued(ws.store) == [], "the phantom's removal was implemented and it is still queued"
    ws.close()


def test_marking_done_with_nothing_handed_off_is_refused_not_a_silent_success(tmp_path):
    """`feature.realized` settles a request. On a feature that was never
    handed off there is none, so the op conflicts."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    op = ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": "h1"}, "payload": {"sha": "abc"}}], actor=H)[0]
    assert op.conflict is not None, op.payload
