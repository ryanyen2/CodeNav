"""The applier: canonical payloads, inverses, conflicts, replay (spec §5, §8, §9)."""
import copy
import random

from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.ids import START
from codoc.calculus.ops import Op
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD, SUGGEST_DEL


class Mint:
    """Mints ops for one session with a monotone clock."""

    def __init__(self, session="s", actor="human:a"):
        self.session, self.actor, self.n = session, actor, 0

    def op(self, kind, target, payload, *, actor=None, plan=None, batch="b", caused_by=None):
        self.n += 1
        return Op(id=f"{self.session}:{self.n}", at=f"{self.n:020d}", actor=actor or self.actor, batch=batch,
                  kind=kind, target=dict(target), payload=dict(payload), plan=plan, caused_by=caused_by)


def heading(m: Mint, store: Store, bid: str, title: str, level=1, after=START, plan=None, actor=None):
    apply(store, m.op(K.BLOCK_ADD, {"bid": bid}, {"kind": HEADING, "level": level, "after": after}, plan=plan, actor=actor))
    return apply(store, m.op(K.TEXT_INSERT, {"bid": bid, "path": "text"}, {"after": START, "text": title}, plan=plan, actor=actor))


def paragraph(m: Mint, store: Store, bid: str, body: str, after, plan=None, actor=None):
    apply(store, m.op(K.BLOCK_ADD, {"bid": bid}, {"kind": PARAGRAPH, "after": after}, plan=plan, actor=actor))
    return apply(store, m.op(K.TEXT_INSERT, {"bid": bid, "path": "text"}, {"after": START, "text": body}, plan=plan, actor=actor))


def replay(store: Store) -> Store:
    fresh = Store(store.policy)
    for o in store.ops:
        apply(fresh, Op.from_json(copy.deepcopy(o.to_json())))
    return fresh


def invert(store: Store, m: Mint, op: Op) -> list[Op]:
    """Apply the recorded inverse as new ops (undo)."""
    out = []
    for body in op.inverse:
        out.append(apply(store, m.op(body["kind"], body["target"], body["payload"])))
    return out


def test_feature_view_and_tree_fold():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    paragraph(m, s, "p1", "body one", after="h1")
    heading(m, s, "h2", "Child", level=3, after="p1")   # level gap tolerated
    paragraph(m, s, "p2", "body two", after="h2")
    heading(m, s, "h3", "Second top", level=1, after="p2")
    assert [b.id for b in s.feature_range("h1")] == ["h1", "p1", "h2", "p2"]
    assert [b.id for b in s.feature_range("h2")] == ["h2", "p2"]
    assert s.feature_of("p2") == "h2" and s.feature_of("p1") == "h1"
    from codoc.calculus.doc import fold_tree
    roots = fold_tree(s.headings())
    assert [r.id for r in roots] == ["h1", "h3"]
    assert [c.id for c in roots[0].children] == ["h2"]


def test_every_op_inverts_and_replays(tmp_path):
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    ins = paragraph(m, s, "p1", "hello world", after="h1")
    # text.delete by range → canonical segments; inverse restores.
    d = apply(s, m.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": [ins.id, 0], "to": [ins.id, 4]}))
    assert d.conflict is None and d.payload["segments"] == [[ins.id, 0, 5]]
    assert s.blocks["p1"].text().live_text() == " world"
    invert(s, m, d)
    assert s.blocks["p1"].text().live_text() == "hello world"
    # mark and inverse
    mk = apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                       {"kind": BOLD, "attrs": {}, "start": [ins.id, 0], "end": [ins.id, 4], "on": True}))
    assert len(s.blocks["p1"].text().marks) == 1
    invert(s, m, mk)
    assert s.blocks["p1"].text().marks == []
    # split then merge (inverse)
    sp = apply(s, m.op(K.BLOCK_SPLIT, {"bid": "p1", "path": "text"}, {"at": [ins.id, 4], "new": "p2"}))
    assert sp.conflict is None
    assert s.blocks["p1"].text().live_text() == "hello" and s.blocks["p2"].text().live_text() == " world"
    assert [b.id for b in s.ordered()] == ["h1", "p1", "p2"]
    invert(s, m, sp)
    assert s.blocks["p1"].text().live_text() == "hello world" and not s.blocks["p2"].live
    # move a feature with its body, inverse restores ranks
    heading(m, s, "h2", "Two", after="p1")
    paragraph(m, s, "p3", "two body", after="h2")
    mv = apply(s, m.op(K.BLOCK_MOVE, {"bid": "h2"}, {"after": START, "level_delta": 1}))
    assert mv.conflict is None
    assert [b.id for b in s.ordered()] == ["h2", "p3", "h1", "p1"]
    assert s.blocks["h2"].level == 2
    invert(s, m, mv)
    assert [b.id for b in s.ordered()] == ["h1", "p1", "h2", "p3"]
    assert s.blocks["h2"].level == 1
    # delete a feature: heading + own body only; inverse restores
    de = apply(s, m.op(K.BLOCK_DELETE, {"bid": "h1"}, {}))
    assert de.payload["ids"] == ["h1", "p1"]
    assert [b.id for b in s.ordered()] == ["h2", "p3"]
    invert(s, m, de)
    assert [b.id for b in s.ordered()] == ["h1", "p1", "h2", "p3"]
    # bindings with displacement
    a1 = apply(s, m.op(K.BIND_ATTACH, {"fid": "h1"}, {"file": "f.py", "symbol": "g", "fingerprint": "x"}))
    a2 = apply(s, m.op(K.BIND_ATTACH, {"fid": "h2"}, {"file": "f.py", "symbol": "g", "fingerprint": "y"}))
    assert s.bindings[("f.py", "g")].feature == "h2" and a2.payload["displaced"]["feature"] == "h1"
    invert(s, m, a2)
    assert s.bindings[("f.py", "g")].feature == "h1"
    # watermarks
    ho = apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {"asked": "make it faster"}))
    assert s.watermarks("h1").handoff == s.frontier or s.watermarks("h1").handoff["s"] <= s.frontier["s"]
    apply(s, m.op(K.FEATURE_REALIZED, {"fid": "h1"}, {"sha": "abc"}))
    invert(s, m, ho)
    assert s.watermarks("h1").handoff == {}
    # I6: replay reproduces the state.
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def test_conflicts_are_recorded_and_state_untouched():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    before = s.state_fingerprint()
    bad = apply(s, m.op(K.TEXT_INSERT, {"bid": "h1", "path": "text"}, {"after": ["nope:1", 0], "text": "x"}))
    assert bad.conflict is not None
    dup = apply(s, m.op(K.BLOCK_ADD, {"bid": "h1"}, {"kind": PARAGRAPH, "after": START}))
    assert dup.conflict == "duplicate_block"
    p = paragraph(m, s, "p1", "x", after="h1")
    mh = apply(s, m.op(K.BLOCK_MERGE, {"into": "p1", "bid": "h1"}, {}))
    assert mh.conflict == "merge_heading"
    assert s.blocks["h1"].live
    assert replay(s).state_fingerprint() == s.state_fingerprint()
    assert len(replay(s).ops) == len(s.ops)


def test_plan_tags_accept_and_reject_keep_attribution():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    ins = paragraph(m, s, "p1", "The client retries once.", after="h1")
    agent = Mint("a", "agent:claude")
    apply(s, agent.op(K.PLAN_OPEN, {"pid": "pl"}, {"kind": "edit", "note": "retries thrice now"}))
    # Suggest: delete "once", insert "three times".
    sd = apply(s, agent.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                           {"kind": SUGGEST_DEL, "attrs": {"plan": "pl"}, "start": [ins.id, 19], "end": [ins.id, 22], "on": True}, plan="pl"))
    assert sd.conflict is None
    si = apply(s, agent.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 22], "text": " three times"}, plan="pl"))
    assert si.conflict is None
    t = s.blocks["p1"].text()
    assert t.live_text() == "The client retries once three times."
    assert [seg.plan for seg in t.segs if seg.op == si.id] == ["pl"]
    # Human types at the edge of the suggestion: not tagged (I2).
    h = apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [si.id, 11], "text": "!"}))
    assert next(seg for seg in t.segs if seg.op == h.id).plan is None
    acc = apply(s, m.op(K.PLAN_ACCEPT, {"pid": "pl"}, {}))
    assert acc.conflict is None
    assert t.live_text() == "The client retries  three times!."
    assert s.plans["pl"].status == "accepted"
    # Attribution survives: the agent still authored the inserted words.
    assert next(seg for seg in t.segs if seg.op == si.id).actor == "agent:claude"
    # Undo the verdict.
    invert(s, m, acc)
    assert s.plans["pl"].status == "open" and t.live_text() == "The client retries once three times!."
    rej = apply(s, m.op(K.PLAN_REJECT, {"pid": "pl"}, {}))
    assert rej.conflict is None
    assert t.live_text() == "The client retries once!."
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def test_delete_plan_strikes_then_accept_deletes():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    paragraph(m, s, "p1", "body", after="h1")
    heading(m, s, "h2", "Two", after="p1")
    loop = Mint("l", "loop")
    apply(s, loop.op(K.PLAN_OPEN, {"pid": "pd"}, {"kind": "delete", "target": "h1", "note": "lost all its code"}))
    ttl = s.blocks["h1"].text()
    apply(s, loop.op(K.TEXT_MARK, {"bid": "h1", "path": "text"},
                     {"kind": SUGGEST_DEL, "attrs": {"plan": "pd"}, "start": ttl.ref_at(0), "end": ttl.ref_at(2), "on": True}, plan="pd"))
    assert [b.id for b in s.ordered()] == ["h1", "p1", "h2"]   # nothing deleted yet
    acc = apply(s, m.op(K.PLAN_ACCEPT, {"pid": "pd"}, {}))
    assert acc.payload["deleted_blocks"] == ["h1", "p1"]
    assert [b.id for b in s.ordered()] == ["h2"]
    invert(s, m, acc)
    assert [b.id for b in s.ordered()] == ["h1", "p1", "h2"]
    assert s.blocks["h1"].text().live_text() == "Top"


def test_plan_cannot_delete_blocks_and_suggest_del_needs_plan():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    agent = Mint("a", "agent:x")
    apply(s, agent.op(K.PLAN_OPEN, {"pid": "p"}, {"kind": "edit"}))
    bad = apply(s, agent.op(K.BLOCK_DELETE, {"bid": "h1"}, {}, plan="p"))
    assert bad.conflict == "not_plannable"
    t = s.blocks["h1"].text()
    bad2 = apply(s, m.op(K.TEXT_MARK, {"bid": "h1", "path": "text"},
                         {"kind": SUGGEST_DEL, "attrs": {"plan": "p"}, "start": t.ref_at(0), "end": t.ref_at(1), "on": True}))
    assert bad2.conflict == "suggest_del_needs_plan"


def test_authority_policy_records_conflict():
    def policy(op: Op):
        if op.actor.startswith("human:contrib") and op.plan is None and op.kind != K.PLAN_OPEN:
            return "suggest role may only write plans"
        return None
    m, s = Mint(), Store(policy)
    heading(m, s, "h1", "Top")
    c = Mint("c", "human:contrib")
    denied = apply(s, c.op(K.TEXT_INSERT, {"bid": "h1", "path": "text"}, {"after": START, "text": "X"}))
    assert denied.conflict == "authority"
    assert s.blocks["h1"].text().live_text() == "Top"


def test_split_heading_yields_heading_plus_paragraph_and_id_follows_text():
    m, s = Mint(), Store()
    ins = heading(m, s, "h1", "Title")
    # Split at START: the empty half is new and goes before; the heading keeps its text.
    sp = apply(s, m.op(K.BLOCK_SPLIT, {"bid": "h1", "path": "text"}, {"at": START, "new": "n1"}))
    assert sp.payload["new_first"] is True
    assert [b.id for b in s.ordered()] == ["n1", "h1"]
    assert s.blocks["n1"].kind == PARAGRAPH and s.blocks["h1"].text().live_text() == "Title"
    invert(s, m, sp)
    assert [b.id for b in s.ordered()] == ["h1"]
    # Split at END: new empty paragraph after.
    sp2 = apply(s, m.op(K.BLOCK_SPLIT, {"bid": "h1", "path": "text"}, {"at": [ins.id, 4], "new": "n2"}))
    assert [b.id for b in s.ordered()] == ["h1", "n2"] and s.blocks["n2"].kind == PARAGRAPH
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def test_random_op_soup_replays_and_inverts():
    rng = random.Random(11)
    m, s = Mint(), Store()
    heading(m, s, "h1", "Root")
    paragraph(m, s, "p1", "seed text here", after="h1")
    for step in range(300):
        blocks = s.ordered()
        b = rng.choice(blocks)
        t = b.text()
        live = list(t.live_refs())
        r = rng.random()
        if r < 0.35 or not live:
            after = START if not live or rng.random() < 0.2 else rng.choice(live)
            op = m.op(K.TEXT_INSERT, {"bid": b.id, "path": "text"},
                      {"after": after if isinstance(after, str) else list(after),
                       "text": "".join(rng.choice("xyz ") for _ in range(rng.randint(1, 4)))})
        elif r < 0.55:
            a, z = sorted(rng.sample(range(len(live)), 2)) if len(live) > 1 else (0, 0)
            op = m.op(K.TEXT_DELETE, {"bid": b.id, "path": "text"}, {"from": list(live[a]), "to": list(live[z])})
        elif r < 0.65:
            a, z = sorted(rng.sample(range(len(live)), 2)) if len(live) > 1 else (0, 0)
            op = m.op(K.TEXT_MARK, {"bid": b.id, "path": "text"},
                      {"kind": BOLD, "attrs": {}, "start": list(live[a]), "end": list(live[z]), "on": rng.random() < 0.7})
        elif r < 0.75:
            at = START if rng.random() < 0.2 else rng.choice(live)
            op = m.op(K.BLOCK_SPLIT, {"bid": b.id, "path": "text"}, {"at": at if isinstance(at, str) else list(at), "new": f"n{step}"})
        elif r < 0.85:
            paras = [x for x in blocks if x.kind == PARAGRAPH]
            if len(paras) < 1:
                continue
            victim = rng.choice(paras)
            i = blocks.index(victim)
            nb = blocks[i - 1] if i > 0 and rng.random() < 0.5 else (blocks[i + 1] if i + 1 < len(blocks) else None)
            if nb is None or nb.kind != PARAGRAPH:
                continue
            op = m.op(K.BLOCK_MERGE, {"into": nb.id, "bid": victim.id}, {})
        elif r < 0.93:
            op = m.op(K.BLOCK_ADD, {"bid": f"a{step}"}, {"kind": rng.choice([PARAGRAPH, HEADING]), "level": rng.randint(1, 3), "after": b.id})
        else:
            target = rng.choice(blocks)
            others = [x for x in blocks if x.id != target.id]
            after = START if not others or rng.random() < 0.2 else rng.choice(others).id
            op = m.op(K.BLOCK_MOVE, {"bid": target.id}, {"after": after, "level_delta": rng.choice([-1, 0, 1])})
        before = copy.deepcopy(s.state_fingerprint())
        done = apply(s, op)
        if done.conflict is not None:
            assert s.state_fingerprint() == before, done.conflict
            continue
        # Inverse round trip on a copy: apply inverse then the op again yields the same state.
        probe = replay(s)
        undo = Mint("u", "human:a")
        for body in done.inverse:
            un = apply(probe, undo.op(body["kind"], body["target"], body["payload"]))
            assert un.conflict is None, (body, un.conflict)
        assert probe.state_fingerprint()["blocks"] and _live_view(probe) == _live_view_from(before), (op.kind, done.payload)
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def _live_view(store: Store):
    return [(b.id, b.kind, b.level, b.text().live_text()) for b in store.ordered()]


def _live_view_from(fp):
    from codoc.calculus.doc import Block
    out = []
    for bj in fp["blocks"]:
        b = Block.from_json(bj)
        if b.live:
            out.append((b.id, b.kind, b.level, b.text().live_text()))
    return out
