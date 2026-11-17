"""Properties of the applier: verdicts and undo resolve by identity under
concurrent structure; a conflict never moves the state; un-applying a suffix
equals replaying the prefix."""
import copy
import random

from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.ids import START
from codoc.calculus.ops import Op
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD, SUGGEST_DEL
from tests.helpers import Mint, T, heading, invert, paragraph, replay

# ── one holder per identity; verdicts find their characters ──────────

def test_merge_moves_characters_and_lookup_follows_them():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    a = paragraph(m, s, "a", "first", after="h1")
    paragraph(m, s, "b", "second", after="a")
    mg = apply(s, m.op(K.BLOCK_MERGE, {"into": "b", "bid": "a"}, {}))
    assert mg.conflict is None and T(s, "b") == "firstsecond"
    assert not s.blocks["a"].text().holds((a.id, 0))                     # the dead block holds nothing
    ins = apply(s, m.op(K.TEXT_INSERT, {"bid": "a", "path": "text"}, {"after": [a.id, 4], "text": "!"}))
    assert ins.conflict is None and T(s, "b") == "first!second"         # the stale hint is corrected
    invert(s, m, ins)
    un = invert(s, m, mg)
    assert un[0].conflict is None and T(s, "a") == "first" and T(s, "b") == "second"


def test_verdicts_resolve_by_identity_under_split_move_merge():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    ins = paragraph(m, s, "p1", "alpha beta gamma", after="h1")
    paragraph(m, s, "p2", "delta", after="p1")
    ag = Mint("a", "agent:x")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "pl"}, {"kind": "edit"}))
    si = apply(s, ag.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 15], "text": " epsilon"}, plan="pl"))
    sd = apply(s, ag.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                        {"kind": SUGGEST_DEL, "attrs": {}, "start": [ins.id, 6], "end": [ins.id, 9], "on": True}, plan="pl"))
    assert si.conflict is None and sd.conflict is None
    # The person restructures around the suggestion.
    assert apply(s, m.op(K.BLOCK_SPLIT, {"bid": "p1", "path": "text"}, {"at": [ins.id, 4], "new": "p3"})).conflict is None
    assert apply(s, m.op(K.BLOCK_MOVE, {"bid": "p3"}, {"after": "p2"})).conflict is None
    assert apply(s, m.op(K.BLOCK_MERGE, {"into": "p2", "bid": "p3"}, {})).conflict is None
    assert T(s, "p2") == "delta beta gamma epsilon"
    snap = replay(s)
    rj = apply(s, m.op(K.PLAN_REJECT, {"pid": "pl"}, {}))
    assert rj.conflict is None
    assert not [g for b in s.blocks.values() for g in b.text().segs if g.plan == "pl" and g.live]
    assert not [mk for b in s.blocks.values() for mk in b.text().marks if mk.kind == SUGGEST_DEL]
    assert T(s, "p2") == "delta beta gamma"
    acc = apply(snap, m.op(K.PLAN_ACCEPT, {"pid": "pl"}, {}))
    assert acc.conflict is None
    assert T(snap, "p2") == "delta  gamma epsilon"
    invert(snap, m, acc)
    assert T(snap, "p2") == "delta beta gamma epsilon" and snap.plans["pl"].status == "open"
    assert replay(s).state_fingerprint() == s.state_fingerprint()
    assert replay(snap).state_fingerprint() == snap.state_fingerprint()


# ── segment deletes kill only the named characters ──────────────────────

def test_redo_of_a_segment_delete_spares_characters_typed_inside_the_range():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    ins = paragraph(m, s, "p1", "hello", after="h1")
    d = apply(s, m.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": [ins.id, 0], "to": [ins.id, 4]}))
    (r,) = invert(s, m, d)
    apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 2], "text": "X"}))
    redo = invert(s, m, r)
    assert redo[0].conflict is None and T(s, "p1") == "X"


# ── a conflict never moves the state ────────────────────────────────────

def test_midway_conflict_leaves_state_untouched():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    ins = paragraph(m, s, "p1", "abc", after="h1")
    before = copy.deepcopy(s.state_fingerprint())
    bad = apply(s, m.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"},
                        {"segments": [[ins.id, 0, 3], ["ghost:1", 0, 1]]}))
    assert bad.conflict is not None and bad.inverse == []
    assert s.state_fingerprint() == before


# ── subsets govern marks; reopen carries the subset ──────────────────

def test_subset_accept_rejects_unaccepted_marks_and_redo_keeps_subset():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    ins = paragraph(m, s, "p1", "keep this", after="h1")
    ag = Mint("a", "agent:x")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "pl"}, {"kind": "edit"}))
    sd = apply(s, ag.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                        {"kind": SUGGEST_DEL, "attrs": {}, "start": [ins.id, 5], "end": [ins.id, 8], "on": True}, plan="pl"))
    i1 = apply(s, ag.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 8], "text": "!"}, plan="pl"))
    i2 = apply(s, ag.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [i1.id, 0], "text": "?"}, plan="pl"))
    acc = apply(s, m.op(K.PLAN_ACCEPT, {"pid": "pl"}, {"subset": [i1.id]}))
    assert sorted(acc.payload["rejected"]) == sorted([sd.id, i2.id])
    assert T(s, "p1") == "keep this!"
    assert not [mk for mk in s.blocks["p1"].text().marks if mk.kind == SUGGEST_DEL]
    (ro,) = invert(s, m, acc)
    assert T(s, "p1") == "keep this!?" and s.plans["pl"].status == "open"
    redo = invert(s, m, ro)
    assert redo[0].conflict is None and T(s, "p1") == "keep this!"


# ── ranks are re-derived on restore ─────────────────────────────────────

def test_move_undo_never_collides_ranks():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    paragraph(m, s, "a", "a", after="h1")
    paragraph(m, s, "c", "c", after="a")
    paragraph(m, s, "b", "b", after="a")
    mv = apply(s, m.op(K.BLOCK_MOVE, {"bid": "b"}, {"after": "c"}))
    paragraph(m, s, "y", "y", after="a")
    invert(s, m, mv)
    ranks = [x.rank for x in s.ordered()]
    assert len(set(ranks)) == len(ranks)
    nxt = apply(s, m.op(K.BLOCK_ADD, {"bid": "z"}, {"kind": PARAGRAPH, "after": "b"}))
    assert nxt.conflict is None


# ── set(kind) keeps the level through undo ──────────────────────────────

def test_set_kind_round_trip_keeps_level():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top", level=3)
    sk = apply(s, m.op(K.BLOCK_SET, {"bid": "h1", "path": "kind"}, {"value": PARAGRAPH}))
    assert s.blocks["h1"].kind == PARAGRAPH and s.blocks["h1"].level == 0
    invert(s, m, sk)
    assert s.blocks["h1"].kind == HEADING and s.blocks["h1"].level == 3


# ── reject undoes every plan op kind, without clobbering later moves ────

def test_reject_undoes_split_and_set_and_respects_later_human_move():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top", level=1)
    ins = paragraph(m, s, "p1", "one", after="h1")
    ag = Mint("a", "agent:x")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "pl"}, {"kind": "edit"}))
    apply(s, ag.op(K.BLOCK_SPLIT, {"bid": "p1", "path": "text"}, {"at": [ins.id, 2], "new": "p2"}, plan="pl"))
    apply(s, ag.op(K.TEXT_INSERT, {"bid": "p2", "path": "text"}, {"after": [ins.id, 2], "text": "xx"}, plan="pl"))
    apply(s, ag.op(K.BLOCK_SET, {"bid": "h1", "path": "level"}, {"value": 3}, plan="pl"))
    rj = apply(s, m.op(K.PLAN_REJECT, {"pid": "pl"}, {}))
    assert rj.conflict is None
    assert not s.blocks["p2"].live or T(s, "p2") != ""
    assert s.blocks["h1"].level == 1
    # A plan move, then the person moves the same block: reject leaves the person's move.
    m2, s2 = Mint(), Store()
    heading(m2, s2, "h1", "Top")
    paragraph(m2, s2, "a", "a", after="h1"); paragraph(m2, s2, "b", "b", after="a"); paragraph(m2, s2, "c", "c", after="b")
    apply(s2, ag.op(K.PLAN_OPEN, {"pid": "pm"}, {"kind": "move"}))
    apply(s2, ag.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "c"}, plan="pm"))
    apply(s2, m2.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "b"}))
    apply(s2, m2.op(K.PLAN_REJECT, {"pid": "pm"}, {}))
    assert [x.id for x in s2.ordered()] == ["h1", "b", "a", "c"]


# ── refusals ───────────────────────────────────────────────────

def test_refusals():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    ins = paragraph(m, s, "p1", "abcd", after="h1")
    heading(m, s, "h9", "Gone", after="p1")
    apply(s, m.op(K.BLOCK_DELETE, {"bid": "h9"}, {}))
    assert apply(s, m.op(K.BLOCK_SPLIT, {"bid": "p1", "path": "text"}, {"at": [ins.id, 1], "new": "h9"})).conflict == "duplicate_block"
    assert apply(s, m.op(K.BIND_ATTACH, {"fid": "h9"}, {"file": "f", "symbol": "s"})).conflict == "dead_block"
    dead_title = s.blocks["h9"].text()
    assert apply(s, m.op(K.TEXT_MARK, {"bid": "h9", "path": "text"},
                         {"kind": BOLD, "attrs": {}, "start": list(dead_title.ref_at(0)), "end": list(dead_title.ref_at(1)), "on": True})).conflict == "dead_block"
    assert apply(s, m.op(K.BLOCK_SET, {"bid": "p1", "path": "rank"}, {"value": "Z"})).conflict == "reserved_path"
    assert apply(s, m.op(K.BLOCK_MERGE, {"into": "h1", "bid": "p1"}, {})).conflict == "merge_heading"
    h1 = apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {"asked": "one"}))
    h2 = apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {"asked": "two"}))
    invert(s, m, h1)
    assert s.watermarks("h1").handoffs == [h2.id]


# ── replay carries the policy ──────────────────────────────────────────

def test_replay_with_policy_keeps_forged_writes_out():
    def policy(store, op):
        return "suggest" if op.actor == "human:contrib" and op.plan is None and op.kind != K.PLAN_OPEN else None
    m, s = Mint(), Store(policy)
    heading(m, s, "h1", "Top")
    c = Mint("c", "human:contrib")
    apply(s, c.op(K.TEXT_INSERT, {"bid": "h1", "path": "text"}, {"after": START, "text": "X"}))
    assert T(s, "h1") == "Top"
    assert replay(s).state_fingerprint() == s.state_fingerprint()


# ── the two fuzz properties ──────────────────────────────────────────

def random_op(rng: random.Random, m: Mint, s: Store, step: int) -> Op:
    blocks = s.ordered()
    b = rng.choice(blocks)
    t = b.text()
    live = list(t.live_refs())
    r = rng.random()
    if r < 0.30 or not live:
        after = START if not live or rng.random() < 0.2 else rng.choice(live)
        return m.op(K.TEXT_INSERT, {"bid": b.id, "path": "text"},
                    {"after": after if isinstance(after, str) else list(after),
                     "text": "".join(rng.choice("xyz ") for _ in range(rng.randint(1, 4)))})
    if r < 0.48:
        a, z = sorted(rng.sample(range(len(live)), 2)) if len(live) > 1 else (0, 0)
        return m.op(K.TEXT_DELETE, {"bid": b.id, "path": "text"}, {"from": list(live[a]), "to": list(live[z])})
    if r < 0.58:
        a, z = sorted(rng.sample(range(len(live)), 2)) if len(live) > 1 else (0, 0)
        return m.op(K.TEXT_MARK, {"bid": b.id, "path": "text"},
                    {"kind": BOLD, "attrs": {}, "start": list(live[a]), "end": list(live[z]), "on": rng.random() < 0.7})
    if r < 0.66:
        at = START if rng.random() < 0.2 else rng.choice(live)
        return m.op(K.BLOCK_SPLIT, {"bid": b.id, "path": "text"}, {"at": at if isinstance(at, str) else list(at), "new": f"n{step}"})
    if r < 0.74:
        paras = [x for x in blocks if x.kind == PARAGRAPH]
        for _ in range(3):
            if not paras:
                break
            v = rng.choice(paras)
            i = blocks.index(v)
            nb = blocks[i - 1] if i > 0 and rng.random() < 0.5 else (blocks[i + 1] if i + 1 < len(blocks) else None)
            if nb is not None and nb.kind == PARAGRAPH:
                return m.op(K.BLOCK_MERGE, {"into": nb.id, "bid": v.id}, {})
        return m.op(K.TEXT_INSERT, {"bid": b.id, "path": "text"}, {"after": START, "text": "q"})
    if r < 0.82:
        return m.op(K.BLOCK_ADD, {"bid": f"a{step}"}, {"kind": rng.choice([PARAGRAPH, HEADING]), "level": rng.randint(1, 3), "after": b.id})
    if r < 0.88:
        target = rng.choice(blocks)
        others = [x for x in blocks if x.id != target.id]
        after = START if not others or rng.random() < 0.2 else rng.choice(others).id
        return m.op(K.BLOCK_MOVE, {"bid": target.id}, {"after": after, "level_delta": rng.choice([-1, 0, 1])})
    if r < 0.93:
        heads = [x for x in blocks if x.kind == HEADING]
        h = rng.choice(heads)
        return m.op(K.BLOCK_SET, {"bid": h.id, "path": "level"}, {"value": rng.randint(1, 4)})
    if r < 0.97:
        victim = rng.choice([x for x in blocks if x.id != "h1"] or blocks)
        return m.op(K.BLOCK_DELETE, {"bid": victim.id}, {})
    heads = [x for x in blocks if x.kind == HEADING]
    return m.op(K.BIND_ATTACH, {"fid": rng.choice(heads).id}, {"file": "f.py", "symbol": rng.choice(["a", "b", "c"])})


def soup(seed: int, steps: int) -> Store:
    rng = random.Random(seed)
    m, s = Mint(), Store()
    heading(m, s, "h1", "Root")
    paragraph(m, s, "p1", "seed text here", after="h1")
    for step in range(steps):
        if not s.ordered():
            break
        apply(s, random_op(rng, m, s, step))
    return s


def test_unapply_suffix_equals_prefix_replay():
    s = soup(3, 220)
    log = [Op.from_json(copy.deepcopy(o.to_json())) for o in s.ops]
    for k in (len(log) // 4, len(log) // 2, len(log) - 3):
        probe = replay(s)
        undo = Mint("u", "human:a")
        for o in reversed(log[k:]):
            if o.conflict is not None:
                continue
            for b in o.inverse:          # an inverse is a list of bodies applied in order
                un = apply(probe, undo.op(b["kind"], b["target"], b["payload"]))
                assert un.conflict is None, (o.kind, o.payload, b, un.conflict)
        prefix = Store()
        for o in log[:k]:
            apply(prefix, Op.from_json(copy.deepcopy(o.to_json())))
        # Undo kills what the suffix minted rather than un-minting it, so the
        # tombstones differ; what must agree is everything a reader can see.
        assert live_view(probe) == live_view(prefix), k
        assert probe.state_fingerprint()["bindings"] == prefix.state_fingerprint()["bindings"], k


def live_view(store: Store):
    from codoc.calculus.derive import live_spans
    out = []
    for b in store.ordered():
        t = b.text()
        spans = sorted((sp.kind, tuple(sorted(sp.attrs.items())), sp.start, sp.end) for sp in live_spans(t)
                       if sp.kind != SUGGEST_DEL)
        out.append((b.id, b.kind, b.level, t.live_text(), spans))
    return out


def test_conflict_never_moves_the_state_fuzz():
    rng = random.Random(5)
    m, s = Mint(), Store()
    heading(m, s, "h1", "Root")
    p = paragraph(m, s, "p1", "seed text", after="h1")
    for step in range(300):
        op = random_op(rng, m, s, step)
        if rng.random() < 0.3:
            keys = [k for k in ("after", "from", "to", "start", "end", "at") if k in op.payload]
            if keys and rng.random() < 0.7:
                op.payload[rng.choice(keys)] = ["ghost:1", 0]
            elif op.kind == K.TEXT_DELETE and rng.random() < 0.5:
                op.payload = {"segments": [[p.id, 0, 2], ["ghost:1", 0, 1]]}
            else:
                op.target = {**op.target, "bid": "ghost"}
        before = copy.deepcopy(s.state_fingerprint())
        done = apply(s, op)
        if done.conflict is not None:
            assert s.state_fingerprint() == before, (op.kind, done.conflict)
            assert done.inverse == []
    assert replay(s).state_fingerprint() == s.state_fingerprint()
