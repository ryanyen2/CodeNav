"""The timeline window un-applies the log backwards on clones (spec §6, §9)."""
import random

from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.store import Store
from codoc.calculus.doc import PARAGRAPH
from codoc.calculus.text import BOLD, SUGGEST_DEL
from codoc.calculus.window import history_window
from tests.calculus.test_apply import Mint, heading, invert, paragraph


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


def check_window(store: Store, limit: int = 1000) -> dict:
    """Every op's reported text equals a prefix replay's, and `before` equals
    the state just before the window."""
    truth = texts_after_each_op(store)
    w = history_window(store, limit=limit)
    for e in w["ops"]:
        snap = truth[e["pos"]]
        for bid, text in e["blocks"].items():
            assert text == snap[bid], (e["kind"], e["pos"], bid, text, snap[bid])
    if w["start"] > 0:
        before = truth[w["start"] - 1]
        for bid, text in w["before"].items():
            assert bid in before and text == before[bid], ("before", bid, text, before.get(bid))
        for bid in w["born"]:
            assert bid not in before, (bid, "born inside the window but existed before it")
    return w


def test_a_segment_delete_whose_characters_a_split_has_since_parted_un_applies_in_both():
    """Insert 'abcdef', delete it, undo (restore), split inside it, then undo
    the restore: one `text.delete{segments}` whose characters now sit in two
    blocks. The window must revive both halves, and the block the op named is
    not where they are."""
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client")
    body = paragraph(m, s, "p1", "Retries once.", after="h1")
    end = s.blocks["p1"].text().last_live_ref()
    i = apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": list(end), "text": "abcdef"}))
    assert i.conflict is None and body.conflict is None
    d = apply(s, m.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": [i.id, 0], "to": [i.id, 5]}))
    r = invert(s, m, d)[0]
    sp = apply(s, m.op(K.BLOCK_SPLIT, {"bid": "p1", "path": "text"}, {"new": "p2", "at": [i.id, 2]}))
    assert sp.conflict is None
    u = invert(s, m, r)[0]
    assert u.conflict is None
    assert s.blocks["p1"].text().live_text() == "Retries once." and s.blocks["p2"].text().live_text() == ""
    check_window(s)


def test_window_equals_prefix_replay_under_every_kind_fuzzed():
    """Random typing, deletes, plan marks and verdicts, splits, merges, block
    deletes and undos — bounded and unbounded windows both reconstruct."""
    for seed in range(30):
        rng = random.Random(seed)
        m, s = Mint(), Store()
        heading(m, s, "h1", "Client")
        paragraph(m, s, "p1", "Retries once.", after="h1")
        paras, plans, undoable = ["p1"], [], []
        for step in range(45):
            pb = rng.choice([p for p in paras if s.blocks[p].live] or ["p1"])
            live = list(s.blocks[pb].text().live_refs())
            r, op = rng.random(), None
            if r < 0.2 and live:
                plan = rng.choice(plans + [None]) if plans else None
                op = apply(s, m.op(K.TEXT_INSERT, {"bid": pb, "path": "text"},
                                   {"after": list(rng.choice(live)), "text": f"w{step} "},
                                   actor="loop" if plan else None, plan=plan))
            elif r < 0.3 and len(live) > 2:
                i, j = sorted(rng.sample(range(len(live)), 2))
                op = apply(s, m.op(K.TEXT_DELETE, {"bid": pb, "path": "text"}, {"from": list(live[i]), "to": list(live[j])}))
            elif r < 0.38 and plans and len(live) > 2:
                i, j = sorted(rng.sample(range(len(live)), 2))
                op = apply(s, m.op(K.TEXT_MARK, {"bid": pb, "path": "text"},
                                   {"kind": SUGGEST_DEL, "attrs": {"plan": plans[-1]}, "start": list(live[i]),
                                    "end": list(live[j]), "on": True}, actor="loop", plan=plans[-1]))
            elif r < 0.45:
                pid = f"pl{step}"
                apply(s, m.op(K.PLAN_OPEN, {"pid": pid}, {"kind": "edit", "target": pb}, actor="loop", batch=pid))
                plans.append(pid)
            elif r < 0.55 and plans:
                pid = plans.pop(rng.randrange(len(plans)))
                op = apply(s, m.op(K.PLAN_ACCEPT if rng.random() < 0.5 else K.PLAN_REJECT, {"pid": pid}, {}))
            elif r < 0.65 and len(live) > 1:
                op = apply(s, m.op(K.BLOCK_SPLIT, {"bid": pb, "path": "text"},
                                   {"new": f"s{step}", "at": list(rng.choice(live))}))
                if op.conflict is None:
                    paras.append(f"s{step}")
            elif r < 0.75:
                prev, nxt = s.live_neighbours_of(pb)
                other = rng.choice([x for x in (prev, nxt) if x and s.blocks[x].kind == PARAGRAPH] or [None])
                if other:
                    op = apply(s, m.op(K.BLOCK_MERGE, {"into": pb, "bid": other, "path": "text"}, {}))
            elif r < 0.82:
                op = apply(s, m.op(K.BLOCK_DELETE, {"bid": rng.choice(paras)}, {}))
            elif undoable:
                for u in invert(s, m, undoable.pop(rng.randrange(len(undoable)))):
                    op = u
            if op is not None and op.conflict is None and op.inverse:
                undoable.append(op)
        check_window(s)
        check_window(s, limit=rng.randint(3, 25))
