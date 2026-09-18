"""The timeline window un-applies the log backwards on clones (spec §6, §9)."""
from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD
from codoc.calculus.window import history_window
from tests.calculus.test_apply import Mint, heading, paragraph


def texts_after_each_op(store: Store) -> list[dict[str, str]]:
    """Ground truth: replay the prefix up to each op and read every block."""
    from codoc.calculus.ops import Op
    import copy
    out = []
    fresh = Store()
    for o in store.ops:
        apply(fresh, Op.from_json(copy.deepcopy(o.to_json())))
        out.append({b.id: b.text().live_text() for b in fresh.all_blocks()})
    return out


def test_window_matches_prefix_replay_through_text_split_merge_and_marks():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client")
    ins = paragraph(m, s, "p1", "Retries once and then stops.", after="h1")
    apply(s, m.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": [ins.id, 8], "to": [ins.id, 11]}))
    apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 7], "text": "twice"}))
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 0], "end": [ins.id, 6], "on": True}))
    sp = apply(s, m.op(K.BLOCK_SPLIT, {"bid": "p1", "path": "text"}, {"new": "p2", "at": [ins.id, 16]}))
    assert sp.conflict is None
    apply(s, m.op(K.TEXT_INSERT, {"bid": "p2", "path": "text"}, {"after": "START", "text": "Then "}))
    mg = apply(s, m.op(K.BLOCK_MERGE, {"into": "p1", "bid": "p2", "path": "text"}, {}))
    assert mg.conflict is None
    d = apply(s, m.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": [ins.id, 0], "to": [ins.id, 6]}))
    apply(s, m.op(*d.inverse[0].values()))       # an undo through the recorded inverse
    truth = texts_after_each_op(s)
    w = history_window(s, limit=100)
    assert w["start"] == 0 and len(w["ops"]) == len(s.ops)
    for e, snap in zip(w["ops"], truth):
        for bid, text in e["blocks"].items():
            assert text == snap[bid], (e["kind"], bid, text, snap[bid])
    assert all(isinstance(e["inverse"], list) for e in w["ops"])


def test_window_is_bounded_and_records_the_text_before_it():
    m, s = Mint(), Store()
    heading(m, s, "h1", "T")
    ins = paragraph(m, s, "p1", "a", after="h1")
    last = ins
    for i in range(10):
        last = apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [last.id, 0], "text": str(i)}))
    w = history_window(s, limit=3)
    assert len(w["ops"]) == 3 and w["start"] == len(s.ops) - 3
    assert w["before"]["p1"] == "a0123456"
    assert [e["blocks"]["p1"] for e in w["ops"]] == ["a01234567", "a012345678", "a0123456789"]
