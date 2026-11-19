"""The timeline window un-applies the log backwards on clones."""
import random

from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.doc import PARAGRAPH
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD, SUGGEST_DEL
from codoc.calculus.window import history_window
from tests.helpers import Mint, heading, invert, paragraph, texts_after_each_op


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
        for bid, fields in e["blocks"].items():
            assert fields.items() <= snap[bid].items(), (e["kind"], bid, fields, snap[bid])
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
    assert w["before"]["p1"] == {"text": "a0123456"}
    assert [e["blocks"]["p1"]["text"] for e in w["ops"]] == ["a01234567", "a012345678", "a0123456789"]


def check_window(store: Store, limit: int = 1000) -> dict:
    """Every op's reported text equals a prefix replay's, and `before` equals
    the state just before the window."""
    truth = texts_after_each_op(store)
    w = history_window(store, limit=limit)
    for e in w["ops"]:
        snap = truth[e["pos"]]
        for bid, fields in e["blocks"].items():
            assert fields.items() <= snap[bid].items(), (e["kind"], e["pos"], bid, fields, snap[bid])
    if w["start"] > 0:
        before = truth[w["start"] - 1]
        for bid, fields in w["before"].items():
            assert bid in before and fields.items() <= before[bid].items(), ("before", bid, fields, before.get(bid))
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
            elif r < 0.26 and plans:
                # A plan taking back some of its own words, or giving back some it took.
                pid = rng.choice(plans)
                segs = [g for b in s.ordered() for g in b.text().segs if g.plan == pid]
                own = [g for g in segs if g.live]
                back = [g for g in segs if not g.live and getattr(s.by_id.get(g.dead), "plan", None) == pid]
                if own and rng.random() < 0.6:
                    g = rng.choice(own)
                    op = apply(s, m.op(K.TEXT_DELETE, {"bid": pb, "path": "text"}, {"segments": [[g.op, g.k0, len(g.chars)]]},
                                       actor="loop", plan=pid))
                elif back:
                    g = rng.choice(back)
                    op = apply(s, m.op(K.TEXT_RESTORE, {"bid": pb, "path": "text"},
                                       {"segments": [[g.op, g.k0, len(g.chars)]], "by": g.dead}, actor="loop", plan=pid))
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
                ops = s.plans[pid].ops
                subset = {"subset": rng.sample(ops, len(ops) // 2)} if ops and rng.random() < 0.5 else {}
                op = apply(s, m.op(K.PLAN_ACCEPT, {"pid": pid}, subset) if rng.random() < 0.5
                           else m.op(K.PLAN_REJECT, {"pid": pid}, {}))
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


def test_the_window_shows_the_words_a_verdict_gave_back():
    """Accepting only a plan's insert gives back the words its own delete took;
    the window reads that from the verdict's steps, and the reopen's."""
    m, s, ag = Mint(), Store(), Mint("a", "agent:x")
    heading(m, s, "h1", "Feat")
    paragraph(m, s, "p1", "Base.", after="h1")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "edit", "target": "h1"}))
    a = apply(s, ag.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"},
                       {"after": list(s.blocks["p1"].text().last_live_ref()), "text": " Foo"}, plan="P"))
    apply(s, ag.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"segments": [[a.id, 0, 4]]}, plan="P"))
    v = apply(s, m.op(K.PLAN_ACCEPT, {"pid": "P"}, {"subset": [a.id]}))
    invert(s, m, v)
    w = check_window(s, limit=2)
    assert [e["blocks"]["p1"]["text"] for e in w["ops"]] == ["Base. Foo", "Base."]
    assert w["before"]["p1"] == {"text": "Base."}


def test_the_window_carries_the_order_a_verdict_undid():
    """A `plan.reject` that undoes a planned move re-ranks blocks, and a page
    from before it is in the order the plan had produced. The window records
    the rank each op displaced, so the reader folds back to it."""
    m, s = Mint(), Store()
    heading(m, s, "h1", "A")
    paragraph(m, s, "p1", "a", after="h1")
    heading(m, s, "h2", "B", after="p1")
    paragraph(m, s, "p2", "b", after="h2")
    apply(s, m.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "move", "target": "h2"}))
    mv = apply(s, m.op(K.BLOCK_MOVE, {"bid": "h2"}, {"after": "START", "level_delta": 0}, plan="P"))
    assert mv.conflict is None and [b.id for b in s.ordered()] == ["h2", "p2", "h1", "p1"]
    rej = apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    assert rej.conflict is None and [b.id for b in s.ordered()] == ["h1", "p1", "h2", "p2"]
    w = history_window(s, limit=1)
    # Entering the window h2 sat before h1; today it sits after it.
    assert w["ranks"]["h2"] < s.blocks["h1"].rank < s.blocks["h2"].rank, (w["ranks"], s.blocks["h2"].rank)


def test_the_window_carries_the_order_a_reopen_undid():
    """The same for a reopen. A reopen re-applies the verdict's own record,
    so its `moved` rows are the ranks it set; it records the ranks it
    displaced as `undid`, and the window folds back to those."""
    m, s = Mint(), Store()
    heading(m, s, "h1", "A")
    paragraph(m, s, "p1", "a", after="h1")
    heading(m, s, "h2", "B", after="p1")
    paragraph(m, s, "p2", "b", after="h2")
    apply(s, m.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "move", "target": "h2"}))
    apply(s, m.op(K.BLOCK_MOVE, {"bid": "h2"}, {"after": "START", "level_delta": 0}, plan="P"))
    rej = apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    assert rej.conflict is None and [b.id for b in s.ordered()] == ["h1", "p1", "h2", "p2"]
    was = s.blocks["h2"].rank                                  # h2 sat last, where the reject put it
    re = apply(s, m.op(K.PLAN_REOPEN, {"pid": "P"}, dict(rej.inverse[0]["payload"])))
    assert re.conflict is None and [b.id for b in s.ordered()] == ["h2", "p2", "h1", "p1"]
    w = history_window(s, limit=1)
    assert w["ops"][0]["kind"] == K.PLAN_REOPEN
    assert w["ranks"].get("h2") == was, (w["ranks"], was)


def test_window_unapplies_verdicts_and_omits_blocks_born_inside_it():
    from codoc.calculus.text import SUGGEST_DEL
    from codoc.calculus.window import history_window
    m, s = Mint(), Store()
    heading(m, s, "h1", "T")
    ins = paragraph(m, s, "p1", "keep this maybe", after="h1")
    apply(s, m.op(K.PLAN_OPEN, {"pid": "pl"}, {"kind": "edit", "target": "h1"}, actor="loop"))
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                  {"kind": SUGGEST_DEL, "attrs": {}, "start": [ins.id, 5], "end": [ins.id, 9], "on": True}, actor="loop", plan="pl"))
    apply(s, m.op(K.PLAN_ACCEPT, {"pid": "pl"}, {}))
    apply(s, m.op(K.PLAN_OPEN, {"pid": "p2"}, {"kind": "edit", "target": "h1"}, actor="loop"))
    pi = apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 14], "text": " later"}, actor="loop", plan="p2"))
    apply(s, m.op(K.PLAN_REJECT, {"pid": "p2"}, {}))
    truth = texts_after_each_op(s)
    w = history_window(s, limit=500)
    for e in w["ops"]:
        for bid, fields in e["blocks"].items():
            assert fields.items() <= truth[e["pos"]][bid].items(), (e["kind"], bid, fields, truth[e["pos"]][bid])
    assert next(e for e in w["ops"] if e["id"] == pi.id)["blocks"]["p1"] == {"text": "keep maybe later"}
    short = history_window(s, limit=2)
    assert "p1" in short["before"]
    m2, s2 = Mint(), Store()
    heading(m2, s2, "h1", "T")
    paragraph(m2, s2, "p1", "one", after="h1")
    assert "p1" not in history_window(s2, limit=2)["before"]     # born inside the window
