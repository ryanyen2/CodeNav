"""Properties of the applier: verdicts and undo resolve by identity under
concurrent structure; a conflict never moves the state; un-applying a suffix
equals replaying the prefix."""
import copy
import random

import pytest

from codoc.calculus import ops as K
from codoc.calculus.apply import apply, replay_recorded
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.ids import START
from codoc.calculus.ops import Op
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD, SUGGEST_DEL
from codoc.workspace.snapshot import settled_prefix, snapshot_ops, state_at
from tests.helpers import Clock, Mint, T, heading, invert, paragraph, replay

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


def test_a_subset_accept_without_the_plans_own_delete_gives_its_words_back():
    """The plan proposed "abc", then took back "b". Accepting only the insert
    keeps "abc"; reopening the verdict kills "b" again, as the plan's delete."""
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    ins = paragraph(m, s, "p1", "x", after="h1")
    ag = Mint("a", "agent:x")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "pl"}, {"kind": "edit"}))
    i1 = apply(s, ag.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": [ins.id, 0], "text": "abc"}, plan="pl"))
    d1 = apply(s, ag.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"segments": [[i1.id, 1, 1]]}, plan="pl"))
    assert d1.conflict is None and T(s, "p1") == "xac"
    acc = apply(s, m.op(K.PLAN_ACCEPT, {"pid": "pl"}, {"subset": [i1.id]}))
    assert acc.payload["rejected"] == [d1.id] and T(s, "p1") == "xabc"
    (ro,) = invert(s, m, acc)
    assert ro.conflict is None and T(s, "p1") == "xac"
    b = next(sg for sg in s.blocks["p1"].text().segs if sg.op == i1.id and sg.k0 == 1)
    assert b.dead == d1.id


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
    # The plan moves one block twice: undoing the second is not "someone moved it since" for the first.
    m3, s3 = Mint(), Store()
    heading(m3, s3, "h1", "Top")
    paragraph(m3, s3, "a", "a", after="h1"); paragraph(m3, s3, "b", "b", after="a"); paragraph(m3, s3, "c", "c", after="b")
    apply(s3, ag.op(K.PLAN_OPEN, {"pid": "pm"}, {"kind": "move"}))
    apply(s3, ag.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "b"}, plan="pm"))
    apply(s3, ag.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "c"}, plan="pm"))
    apply(s3, m3.op(K.PLAN_REJECT, {"pid": "pm"}, {}))
    assert [x.id for x in s3.ordered()] == ["h1", "a", "b", "c"]


def _four() -> tuple[Mint, Store]:
    m, s = Mint("s", "human:a"), Store()
    heading(m, s, "h1", "Top")
    for x, a in (("a", "h1"), ("b", "a"), ("c", "b"), ("d", "c")):
        paragraph(m, s, x, x, after=a)
    return m, s


def test_a_move_made_between_two_plan_moves_stands_on_reject():
    """The plan moves a, B moves a, the plan moves a again. The reject puts
    back the plan's second move, to where B left a, and not its first, which
    B's move came after."""
    m, s = _four()
    ag, b = Mint("g", "agent:x"), Mint("B", "human:b")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "pm"}, {"kind": "move"}))
    apply(s, ag.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "b"}, plan="pm"))
    apply(s, b.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "d"}))
    apply(s, ag.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "c"}, plan="pm"))
    apply(s, m.op(K.PLAN_REJECT, {"pid": "pm"}, {}))
    assert [x.id for x in s.ordered()] == ["h1", "b", "c", "d", "a"]


def test_a_move_made_after_a_reject_survives_its_undo_and_redo():
    m, s = _four()
    ag, b = Mint("g", "agent:x"), Mint("B", "human:b")
    apply(s, ag.op(K.PLAN_OPEN, {"pid": "pm"}, {"kind": "move"}))
    apply(s, ag.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "c"}, plan="pm"))
    rj = apply(s, m.op(K.PLAN_REJECT, {"pid": "pm"}, {}))
    apply(s, b.op(K.BLOCK_MOVE, {"bid": "a"}, {"after": "d"}))
    (ro,) = invert(s, m, rj)
    assert ro.conflict is None and [x.id for x in s.ordered()] == ["h1", "b", "c", "d", "a"]
    (rd,) = invert(s, m, ro)
    assert rd.conflict is None and [x.id for x in s.ordered()] == ["h1", "b", "c", "d", "a"]


# ── refusals ───────────────────────────────────────────────────

def test_refusals():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Top")
    ins = paragraph(m, s, "p1", "abcd", after="h1")
    heading(m, s, "h9", "Gone", after="p1")
    apply(s, m.op(K.BLOCK_DELETE, {"bid": "h9"}, {}))
    assert apply(s, m.op(K.BLOCK_SPLIT, {"bid": "p1", "path": "text"}, {"at": [ins.id, 1], "new": "h9"})).conflict == "duplicate_block"
    assert apply(s, m.op(K.BIND_ATTACH, {"fid": "h9"}, {"symbol": "s"})).conflict == "dead_block"
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


# ── the fuzz properties ──────────────────────────────────────────────

AGENT = "agent:x"


def plan_op(rng: random.Random, m: Mint, s: Store, step: int, undos: bool) -> Op:
    """Plan work: open a plan, insert words under it, take some back and give
    some back, propose a cut or a kind, level or field change, and decide it.
    With ``undos``, also reopen a verdict or redo one through the inverses
    recorded on the log."""
    if undos and rng.random() < 0.15:
        last = {o.target["pid"]: o for o in s.ops if o.kind in K.TURNS and o.conflict is None and o.inverse}
        if last:
            (inv,) = rng.choice(list(last.values())).inverse
            return m.op(inv["kind"], inv["target"], inv["payload"])
    if not s.open_plans:
        return m.op(K.PLAN_OPEN, {"pid": f"P{step}"}, {"kind": "edit", "target": "h1"}, actor=AGENT)
    pid = rng.choice(sorted(s.open_plans))
    blocks = s.ordered()
    r = rng.random()
    if r < 0.30:
        b = rng.choice(blocks)
        live = list(b.text().live_refs())
        after = list(rng.choice(live)) if live else START
        return m.op(K.TEXT_INSERT, {"bid": b.id, "path": "text"}, {"after": after, "text": rng.choice(["ab", " cde", "f"])},
                    actor=AGENT, plan=pid)
    if r < 0.45:
        own = [(b, g) for b in blocks for g in b.text().segs if g.plan == pid and g.live]
        if own:
            b, g = rng.choice(own)
            k = rng.randrange(len(g.chars))
            return m.op(K.TEXT_DELETE, {"bid": b.id, "path": "text"},
                        {"segments": [[g.op, g.k0 + k, rng.randint(1, len(g.chars) - k)]]}, actor=AGENT, plan=pid)
    if r < 0.55:
        back = [(b, g) for b in blocks for g in b.text().segs if g.plan == pid and not g.live
                and (d := s.by_id.get(g.dead)) is not None and d.plan == pid and d.kind == K.TEXT_DELETE]
        if back:
            b, g = rng.choice(back)
            return m.op(K.TEXT_RESTORE, {"bid": b.id, "path": "text"},
                        {"segments": [[g.op, g.k0, len(g.chars)]], "by": g.dead}, actor=AGENT, plan=pid)
    if r < 0.70:
        prose = [b for b in blocks if b.kind in (PARAGRAPH, "bullet", "numbered")]
        b = rng.choice(prose or blocks)
        q = rng.random()
        if q < 0.4 and prose:
            payload, path = {"value": rng.choice([x for x in (PARAGRAPH, "bullet", "numbered") if x != b.kind])}, "kind"
        elif q < 0.6 and b.kind != PARAGRAPH:
            payload, path = {"value": rng.randint(1, 4)}, "level"
        else:
            payload, path = ({"unset": True} if rng.random() < 0.4 else {"value": rng.randint(0, 9)}), "note"
        return m.op(K.BLOCK_SET, {"bid": b.id, "path": path}, payload, actor=AGENT, plan=pid)
    if r < 0.78:
        # A cut proposal. Its undo names it by the op that placed it, so a
        # split since that cut it in two does not stop the undo.
        b = rng.choice(blocks)
        live = list(b.text().live_refs())
        if len(live) > 1:
            a, z = sorted(rng.sample(range(len(live)), 2))
            return m.op(K.TEXT_MARK, {"bid": b.id, "path": "text"},
                        {"kind": SUGGEST_DEL, "attrs": {}, "start": list(live[a]), "end": list(live[z]), "on": True},
                        actor=AGENT, plan=pid)
    if rng.random() < 0.4:
        return m.op(K.PLAN_REJECT, {"pid": pid}, {})
    ops = s.plans[pid].ops
    payload = {"subset": rng.sample(ops, rng.randint(0, len(ops)))} if ops and rng.random() < 0.5 else {}
    return m.op(K.PLAN_ACCEPT, {"pid": pid}, payload)


def random_op(rng: random.Random, m: Mint, s: Store, step: int, plans: bool = False, undos: bool = False) -> Op:
    if plans and rng.random() < 0.35:
        return plan_op(rng, m, s, step, undos)
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
    return m.op(K.BIND_ATTACH, {"fid": rng.choice(heads).id}, {"symbol": rng.choice(["a", "b", "c"])})


def soup(seed: int, steps: int, plans: bool = False) -> Store:
    rng = random.Random(seed)
    m, s = Mint(), Store()
    heading(m, s, "h1", "Root")
    paragraph(m, s, "p1", "seed text here", after="h1")
    for step in range(steps):
        if not s.ordered():
            break
        apply(s, random_op(rng, m, s, step, plans=plans))
    return s


@pytest.mark.parametrize("seed,plans", [(3, False), (3, True), (8, True), (13, True)])
def test_unapply_suffix_equals_prefix_replay(seed, plans):
    s = soup(seed, 220, plans)
    if plans:
        assert sum(o.kind in (K.PLAN_ACCEPT, K.PLAN_REJECT) and o.conflict is None for o in s.ops) > 3
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
        fields = {k: v for k, v in b.content.items() if k not in b.layout.text}
        out.append((b.id, b.kind, b.level, t.live_text(), spans, fields))
    return out


def test_conflict_never_moves_the_state_fuzz():
    rng = random.Random(5)
    m, s = Mint(), Store()
    heading(m, s, "h1", "Root")
    p = paragraph(m, s, "p1", "seed text", after="h1")
    for step in range(400):
        if not s.ordered():
            break
        op = random_op(rng, m, s, step, plans=True, undos=True)
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


@pytest.mark.parametrize("seed", range(16))
def test_a_verdict_then_its_reopen_is_the_document_before_it_fuzzed(seed):
    """Undoing a verdict straight after it gives back the exact state before
    it, tombstones included, and redoing it gives a reader what the verdict
    did. The plans insert, take back and give back their own words and change
    kinds, levels and fields: the cases whose order a record by category lost.
    Headings made prose and back with code bound between are the case where a
    feature leaves and comes back, and what the block kept of its code then is
    state the reopen gives back too (seeds 7, 11 and 15 caught that)."""
    rng = random.Random(seed)
    m, s = Mint(), Store()
    heading(m, s, "h1", "Root")
    paragraph(m, s, "p1", "seed text here", after="h1")
    checked = 0
    for step in range(200):
        if not s.ordered():
            break
        op = _fold_soup_op(rng, m, s, step) if seed % 2 else random_op(rng, m, s, step, plans=True, undos=True)
        before = copy.deepcopy(s.state_fingerprint()) if op.kind in K.TURNS else None
        done = apply(s, op)
        if before is None or done.conflict is not None:
            continue
        probe, undo = replay(s), Mint("u", "human:a")
        (back,) = invert(probe, undo, done)
        assert back.conflict is None, (seed, step, back.conflict)
        if done.kind != K.PLAN_REOPEN:
            # A redo kills as itself, so only a verdict's reopen restores the tombstones too.
            assert probe.state_fingerprint() == before, (seed, step, done.kind)
        (again,) = invert(probe, undo, back)
        assert again.conflict is None and live_view(probe) == live_view(s), (seed, step, done.kind)
        checked += 1
    assert checked > 5, seed
    assert replay(s).state_fingerprint() == s.state_fingerprint()


@pytest.mark.parametrize("seed", [13, 15, 21, 29, 38])
def test_a_fold_then_the_rest_of_the_log_replays_to_the_live_store(seed):
    """Fold a prefix that leaves no plan in flight across the cut (none open
    at it, none answered or reopened after it: what `settled_prefix` keeps
    to), replay the rest as recorded, and get the same store. Nothing replayed
    after the cut needs an op the fold archived. A cut with a plan in flight
    across it cannot be folded at all: a verdict after it judges the plan's
    ops, which the fold would take."""
    rng = random.Random(seed)
    m, s = Mint(), Store()
    heading(m, s, "h1", "Root")
    paragraph(m, s, "p1", "seed text here", after="h1")
    for step in range(160):
        if not s.ordered():
            break
        apply(s, random_op(rng, m, s, step, plans=True, undos=True))
    folds = 0
    for cut in [*range(len(s.ops) // 4, len(s.ops), 2), settled_prefix(s)]:
        prefix = state_at(s, cut)
        if prefix.open_plans or any(o.kind in K.TURNS and o.target.get("pid") in prefix.plans for o in s.ops[cut:]):
            assert cut != settled_prefix(s), cut
            continue
        folded = Store()
        for d in snapshot_ops(prefix, "snap", Clock()):
            assert replay_recorded(folded, Op.from_json(d)).conflict is None
        for o in s.ops[cut:]:
            replay_recorded(folded, Op.from_json(copy.deepcopy(o.to_json())))
        assert folded.state_fingerprint() == s.state_fingerprint(), (seed, cut)
        folds += 1
    assert folds > 2, seed


def _fold_soup_op(rng: random.Random, m: Mint, s: Store, step: int) -> Op:
    """`random_op`, plus plans on any heading, so a fold can stop part way,
    and moves under them, so "their move stands" meets a fold. Headings are
    made prose and back and code is bound, on the trunk and in plans, and the
    newest op is undone under its own plan as the drain tags a suggester's
    ⌘Z, so a feature leaving and coming back meets a verdict and a fold."""
    if not s.open_plans and rng.random() < 0.3:
        target = rng.choice(s.headings()).id
        return m.op(K.PLAN_OPEN, {"pid": f"Q{step}"}, {"kind": "edit", "target": target}, actor=AGENT)
    blocks = s.ordered()
    plan = rng.choice(sorted(s.open_plans)) if s.open_plans and rng.random() < 0.5 else None
    who = {"actor": AGENT, "plan": plan} if plan else {}
    r = rng.random()
    if plan and r < 0.1:
        b = rng.choice(blocks)
        others = [x.id for x in blocks if x.id != b.id]
        return m.op(K.BLOCK_MOVE, {"bid": b.id}, {"after": rng.choice(others) if others else START}, **who)
    flip = [x for x in blocks if x.kind in (HEADING, PARAGRAPH) and x.id != "h1"]   # h1 stays the root
    if r < 0.2 and flip:
        b = rng.choice(flip)
        return m.op(K.BLOCK_SET, {"bid": b.id, "path": "kind"},
                    {"value": PARAGRAPH if b.kind == HEADING else HEADING, "level": 2}, **who)
    if r < 0.3:
        return m.op(K.BIND_ATTACH, {"fid": rng.choice(s.headings()).id}, {"symbol": rng.choice("abcd")}, **who)
    if r < 0.35:
        last = next((o for o in reversed(s.ops) if o.conflict is None and o.kind not in K.PLAN_KINDS), None)
        if last is not None and last.inverse and (not last.plan or last.plan in s.open_plans):
            inv = last.inverse[0]
            return m.op(inv["kind"], inv["target"], inv["payload"], actor=last.actor, plan=last.plan)
    return random_op(rng, m, s, step, plans=True, undos=True)


def _code_on_live_headings(s: Store) -> bool:
    return all(s.blocks[b.feature].live and s.blocks[b.feature].kind == HEADING for b in s.bindings.values())


@pytest.mark.parametrize("seed", [5, 14, 20, 32])
def test_a_folded_store_goes_on_as_the_unfolded_one(seed):
    """Fold at `settled_prefix` mid-run, then send the same ops to the folded
    store and to the unfolded one: each applies or conflicts alike and leaves
    the same state. Undo reads only the folded store's inverses, and a fold's
    ops have none. The folded log rebuilt from its records, which is what the
    next fold replays, is the folded store."""
    rng, clock = random.Random(seed), Clock()
    m, s = Mint(f"s{seed}", "human:a", clock), Store()
    heading(m, s, "h1", "Root")
    paragraph(m, s, "p1", "seed text here", after="h1")
    heading(m, s, "h2", "Second", after="p1")
    paragraph(m, s, "p2", "more words", after="h2")
    step, cut = 0, 0
    while step < 150 or cut <= len(s.ops) // 3:
        assert step < 400 and s.ordered(), seed
        apply(s, _fold_soup_op(rng, m, s, step))
        assert _code_on_live_headings(s), (seed, step)
        step += 1
        cut = settled_prefix(s) if step >= 150 else 0
    f = Store()
    for d in snapshot_ops(state_at(s, cut), "snap", clock):
        got = replay_recorded(f, Op.from_json(d))
        assert got.conflict is None and got.inverse == [], (d["kind"], got.conflict)
    for o in s.ops[cut:]:
        replay_recorded(f, Op.from_json(copy.deepcopy(o.to_json())))
    for step in range(step, step + 150):
        if not f.ordered():
            break
        op = _fold_soup_op(rng, m, f, step)
        a = apply(f, Op.from_json(copy.deepcopy(op.to_json())))
        b = apply(s, op)
        assert a.conflict == b.conflict and f.state_fingerprint() == s.state_fingerprint(), (seed, step, op.kind)
        assert _code_on_live_headings(s), (seed, step)
    assert replay(f).state_fingerprint() == f.state_fingerprint(), seed


def test_a_fold_carries_the_code_a_reopen_kept_on_a_dead_heading():
    """A reject took code off a block a plan made a heading, the block died as
    prose, and a reopen made it a heading while dead, which keeps that code on
    the block. The plan is then accepted and the log folded. The delete's undo
    re-attaches the code on the folded store as on the unfolded one."""
    clock = Clock()
    m, s = Mint("s", "human:a", clock), Store()
    heading(m, s, "h1", "Root")
    paragraph(m, s, "x", "would be a feature", after="h1")
    apply(s, m.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "edit", "target": "h1"}, actor=AGENT))
    apply(s, m.op(K.BLOCK_SET, {"bid": "x", "path": "kind"}, {"value": HEADING, "level": 2}, actor=AGENT, plan="P"))
    apply(s, m.op(K.BIND_ATTACH, {"fid": "x"}, {"symbol": "a"}))
    rej = apply(s, m.op(K.PLAN_REJECT, {"pid": "P"}, {}))
    dele = apply(s, m.op(K.BLOCK_DELETE, {"bid": "x"}, {}))
    invert(s, m, rej)
    assert apply(s, m.op(K.PLAN_ACCEPT, {"pid": "P"}, {})).conflict is None and settled_prefix(s) == len(s.ops)
    f = Store()
    for d in snapshot_ops(s, "snap", clock):
        assert replay_recorded(f, Op.from_json(d)).conflict is None
    assert f.state_fingerprint() == s.state_fingerprint() and s.bindings == {}
    (inv,) = dele.inverse
    undo = m.op(inv["kind"], inv["target"], inv["payload"])
    assert apply(f, Op.from_json(copy.deepcopy(undo.to_json()))).conflict is None and apply(s, undo).conflict is None
    assert s.bindings["a"].feature == "x" and f.state_fingerprint() == s.state_fingerprint()


@pytest.mark.parametrize("seed", range(200))
def test_rejecting_every_plan_on_a_field_in_any_order_restores_it_fuzz(seed):
    """Several plans set one JSON field, interleaved, some rejected on the way.
    While one is open the ``rejected`` read is the field from before the plans;
    rejecting the rest in any order puts that value back."""
    from codoc.calculus.derive import rejected_content
    rng = random.Random(seed)
    m, s = Mint(f"r{seed}"), Store()
    heading(m, s, "h1", "Root")
    paragraph(m, s, "p1", "text", after="h1")
    if rng.random() < 0.5:
        apply(s, m.op(K.BLOCK_SET, {"bid": "p1", "path": "note"}, {"value": "orig"}))

    def note(content):
        return content.get("note", ("absent",))

    start = note(s.blocks["p1"].content)
    for i in range(rng.randint(2, 4)):
        apply(s, m.op(K.PLAN_OPEN, {"pid": f"P{i}"}, {"kind": "edit", "target": "p1"}, actor=AGENT))
    for step in range(rng.randint(2, 10)):
        if not s.open_plans:
            break
        pid = rng.choice(sorted(s.open_plans))
        if rng.random() < 0.7:
            pl = {"unset": True} if rng.random() < 0.2 else {"value": rng.choice(["a", "b", "c", f"x{step}"])}
            apply(s, m.op(K.BLOCK_SET, {"bid": "p1", "path": "note"}, pl, actor=AGENT, plan=pid))
        else:
            apply(s, m.op(K.PLAN_REJECT, {"pid": pid}, {}))
    b = s.blocks["p1"]
    if any(e[2] in s.open_plans for e in b.prior.get("note", [])):
        assert note(rejected_content(s, b)) == start, seed
    for pid in rng.sample(sorted(s.open_plans), len(s.open_plans)):
        apply(s, m.op(K.PLAN_REJECT, {"pid": pid}, {}))
    assert note(b.content) == start, (seed, b.prior)
    assert replay(s).state_fingerprint() == s.state_fingerprint()


@pytest.mark.parametrize("seed", range(200))
def test_rejecting_every_plan_on_a_kind_or_level_in_any_order_restores_it_fuzz(seed):
    """Several plans set one block's kind and level, interleaved, some rejected
    on the way. Rejecting the rest in any order leaves the kind, the level and
    the code from before any of them, as it does a JSON field: two plans whose
    kind sets cancel are not undone into a kind neither started from."""
    from codoc.calculus.doc import BULLET, NUMBERED
    rng = random.Random(seed)
    m, s = Mint(f"k{seed}"), Store()
    heading(m, s, "h1", "Root")
    paragraph(m, s, "p1", "text", after="h1")
    if rng.random() < 0.5:
        apply(s, m.op(K.BLOCK_SET, {"bid": "p1", "path": "kind"}, {"value": rng.choice([HEADING, BULLET]),
                                                                    "level": rng.randint(1, 3)}))
    if s.blocks["p1"].kind == HEADING:
        apply(s, m.op(K.BIND_ATTACH, {"fid": "p1"}, {"symbol": "a"}))
    b = s.blocks["p1"]
    start = (b.kind, b.level, sorted(s.bindings))
    for i in range(rng.randint(2, 4)):
        apply(s, m.op(K.PLAN_OPEN, {"pid": f"P{i}"}, {"kind": "edit", "target": "p1"}, actor=AGENT))
    for step in range(rng.randint(2, 12)):
        if not s.open_plans:
            break
        pid = rng.choice(sorted(s.open_plans))
        if rng.random() < 0.75:
            if rng.random() < 0.5:
                pl = {"value": rng.choice([HEADING, PARAGRAPH, BULLET, NUMBERED])}
                if rng.random() < 0.5:
                    pl["level"] = rng.randint(1, 4)
                apply(s, m.op(K.BLOCK_SET, {"bid": "p1", "path": "kind"}, pl, actor=AGENT, plan=pid))
            else:
                apply(s, m.op(K.BLOCK_SET, {"bid": "p1", "path": "level"}, {"value": rng.randint(1, 4)},
                              actor=AGENT, plan=pid))
        else:
            apply(s, m.op(K.PLAN_REJECT, {"pid": pid}, {}))
    for pid in rng.sample(sorted(s.open_plans), len(s.open_plans)):
        apply(s, m.op(K.PLAN_REJECT, {"pid": pid}, {}))
    assert (b.kind, b.level, sorted(s.bindings)) == start, seed
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def test_rejecting_a_plan_that_unbound_a_heading_gives_the_code_back_though_another_made_it_one_again():
    """P1 makes a bound heading prose, so its code comes off; P2, opened later,
    makes it a heading again. Rejecting P1 and accepting P2 leaves the heading
    with the code P1 took, as if P1 had never been."""
    m, s = Mint("c"), Store()
    heading(m, s, "h1", "Root")
    heading(m, s, "h2", "Two", level=2, after="h1")
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h2"}, {"symbol": "a"}))
    for pid in ("P1", "P2"):
        apply(s, m.op(K.PLAN_OPEN, {"pid": pid}, {"kind": "edit", "target": "h2"}, actor="agent:x"))
    apply(s, m.op(K.BLOCK_SET, {"bid": "h2", "path": "kind"}, {"value": PARAGRAPH}, actor="agent:x", plan="P1"))
    apply(s, m.op(K.BLOCK_SET, {"bid": "h2", "path": "kind"}, {"value": HEADING, "level": 2},
                  actor="agent:x", plan="P2"))
    assert "a" not in s.bindings
    apply(s, m.op(K.PLAN_REJECT, {"pid": "P1"}, {}))
    apply(s, m.op(K.PLAN_ACCEPT, {"pid": "P2"}, {}))
    assert s.blocks["h2"].kind == HEADING
    assert s.bindings.get("a") and s.bindings["a"].feature == "h2", sorted(s.bindings)
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def _note_plans(realize_after: str) -> tuple[Mint, Store]:
    """The owner's ``note`` is O. Plan P sets A; with ``realize_after="P"`` P is
    accepted, else the owner sets B and back to A by hand. The feature is
    realized, then plan Q sets C."""
    m, s = Mint("z"), Store()
    heading(m, s, "h1", "Root")
    paragraph(m, s, "p1", "text", after="h1")
    apply(s, m.op(K.BLOCK_SET, {"bid": "p1", "path": "note"}, {"value": "O"}))
    apply(s, m.op(K.PLAN_OPEN, {"pid": "P"}, {"kind": "edit", "target": "p1"}, actor=AGENT))
    apply(s, m.op(K.BLOCK_SET, {"bid": "p1", "path": "note"}, {"value": "A"}, actor=AGENT, plan="P"))
    if realize_after == "P":
        apply(s, m.op(K.PLAN_ACCEPT, {"pid": "P"}, {}))
    else:
        for v in ("B", "A"):
            apply(s, m.op(K.BLOCK_SET, {"bid": "p1", "path": "note"}, {"value": v}))
    assert apply(s, m.op(K.FEATURE_HANDOFF, {"fid": "h1"}, {})).conflict is None
    assert apply(s, m.op(K.FEATURE_REALIZED, {"fid": "h1"}, {})).conflict is None
    apply(s, m.op(K.PLAN_OPEN, {"pid": "Q"}, {"kind": "edit", "target": "p1"}, actor=AGENT))
    apply(s, m.op(K.BLOCK_SET, {"bid": "p1", "path": "note"}, {"value": "C"}, actor=AGENT, plan="Q"))
    return m, s


def test_an_accepted_plans_value_is_a_proposal_again_when_the_accept_is_undone_after_a_realization():
    """A realization covers the accepted P; undoing the accept makes P's A a
    proposal again. It is not read as the owner's, and rejecting both plans
    puts the owner's O back: a plan's entry in ``Block.prior`` is never pruned."""
    from codoc.calculus.derive import rejected_content
    m, s = _note_plans("P")
    (back,) = invert(s, m, s.by_id[s.plans["P"].verdict])
    assert back.conflict is None and s.plans["P"].status == "open"
    assert rejected_content(s, s.blocks["p1"])["note"] == "O"
    for pid in ("P", "Q"):
        apply(s, m.op(K.PLAN_REJECT, {"pid": pid}, {}))
    assert s.blocks["p1"].content["note"] == "O"
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def test_a_realization_does_not_let_the_walk_pass_the_owners_set_to_an_older_plans():
    """The owner set the field back to A by hand over open P's A, and the
    feature was realized. The owner's sets are not pruned while P's older
    entry stays, so the walk stops at the owner's A: the ``rejected`` read and
    rejecting both plans give A, not the O from before P."""
    from codoc.calculus.derive import rejected_content
    m, s = _note_plans("owner")
    assert rejected_content(s, s.blocks["p1"])["note"] == "A"
    for pid in ("P", "Q"):
        apply(s, m.op(K.PLAN_REJECT, {"pid": pid}, {}))
    assert s.blocks["p1"].content["note"] == "A", s.blocks["p1"].prior
    assert replay(s).state_fingerprint() == s.state_fingerprint()


def test_rejecting_a_plan_that_unbound_a_heading_gives_the_code_back_past_two_rejected_sets_that_cancel():
    """X made the heading prose and Y made it a heading again; both were
    rejected, then code was bound and the log folded. Q makes it prose and is
    rejected: the walk passes Q, Y and X, and the code Q took off comes back,
    on the unfolded store as on the folded one, which no longer has X and Y."""
    clock = Clock()
    m, s = Mint("c", clock=clock), Store()
    heading(m, s, "h1", "Root")
    heading(m, s, "h2", "Two", level=2, after="h1")
    for pid in ("X", "Y"):
        apply(s, m.op(K.PLAN_OPEN, {"pid": pid}, {"kind": "edit", "target": "h2"}, actor=AGENT))
    apply(s, m.op(K.BLOCK_SET, {"bid": "h2", "path": "kind"}, {"value": PARAGRAPH}, actor=AGENT, plan="X"))
    apply(s, m.op(K.BLOCK_SET, {"bid": "h2", "path": "kind"}, {"value": HEADING, "level": 2}, actor=AGENT, plan="Y"))
    for pid in ("X", "Y"):
        apply(s, m.op(K.PLAN_REJECT, {"pid": pid}, {}))
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h2"}, {"symbol": "a"}))
    assert settled_prefix(s) == len(s.ops)
    f = Store()
    for d in snapshot_ops(s, "snap", clock):
        replay_recorded(f, Op.from_json(d))
    for o in [m.op(K.PLAN_OPEN, {"pid": "Q"}, {"kind": "edit", "target": "h2"}, actor=AGENT),
              m.op(K.BLOCK_SET, {"bid": "h2", "path": "kind"}, {"value": PARAGRAPH}, actor=AGENT, plan="Q"),
              m.op(K.PLAN_REJECT, {"pid": "Q"}, {})]:
        apply(f, Op.from_json(copy.deepcopy(o.to_json())))
        apply(s, o)
    for st in (s, f):
        assert st.blocks["h2"].kind == HEADING and sorted(st.bindings) == ["a"], sorted(st.bindings)
    assert f.state_fingerprint() == s.state_fingerprint()


def _bound_h2() -> tuple[Mint, Store]:
    m, s = Mint("c"), Store()
    heading(m, s, "h1", "Root")
    heading(m, s, "h2", "Two", level=2, after="h1")
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h2"}, {"symbol": "a"}))
    return m, s


@pytest.mark.parametrize("order", ["AB", "BA"])
def test_a_rejected_plans_attach_does_not_come_back_with_the_heading(order):
    """A binds c to the heading; B makes it prose, taking a and c off. However
    the two are rejected, the heading gets back a, and not c, which only a
    rejected plan bound: undoing A's attach drops what the block kept of c."""
    m, s = _bound_h2()
    for pid in "AB":
        apply(s, m.op(K.PLAN_OPEN, {"pid": pid}, {"kind": "edit", "target": "h2"}, actor=AGENT))
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h2"}, {"symbol": "c"}, actor=AGENT, plan="A"))
    apply(s, m.op(K.BLOCK_SET, {"bid": "h2", "path": "kind"}, {"value": PARAGRAPH}, actor=AGENT, plan="B"))
    for pid in order:
        apply(s, m.op(K.PLAN_REJECT, {"pid": pid}, {}))
    assert s.blocks["h2"].kind == HEADING and sorted(s.bindings) == ["a"], sorted(s.bindings)
    assert replay(s).state_fingerprint() == s.state_fingerprint()


@pytest.mark.parametrize("order", ["PQ", "QP"])
def test_the_owners_binding_survives_rejecting_every_plan_after_a_plan_bound_it_again(order):
    """P makes the heading prose (a comes off) and a heading again; Q binds a
    again and makes it prose. Rejecting both, in either order, gives the
    heading back a as the owner bound it, not as Q did."""
    m, s = _bound_h2()
    for pid in "PQ":
        apply(s, m.op(K.PLAN_OPEN, {"pid": pid}, {"kind": "edit", "target": "h2"}, actor=AGENT))
    apply(s, m.op(K.BLOCK_SET, {"bid": "h2", "path": "kind"}, {"value": PARAGRAPH}, actor=AGENT, plan="P"))
    apply(s, m.op(K.BLOCK_SET, {"bid": "h2", "path": "kind"}, {"value": HEADING, "level": 2}, actor=AGENT, plan="P"))
    apply(s, m.op(K.BIND_ATTACH, {"fid": "h2"}, {"symbol": "a"}, actor=AGENT, plan="Q"))
    apply(s, m.op(K.BLOCK_SET, {"bid": "h2", "path": "kind"}, {"value": PARAGRAPH}, actor=AGENT, plan="Q"))
    for pid in order:
        apply(s, m.op(K.PLAN_REJECT, {"pid": pid}, {}))
    assert s.blocks["h2"].kind == HEADING and sorted(s.bindings) == ["a"], sorted(s.bindings)
    assert s.bindings["a"].set_by == m.actor
    assert replay(s).state_fingerprint() == s.state_fingerprint()


@pytest.mark.parametrize("seed", range(200))
def test_rejecting_every_plan_that_binds_code_or_changes_a_kind_restores_the_code_fuzz(seed):
    """Plans make a bound heading another kind and back and bind code to it,
    interleaved, some decided on the way; with ``trunk``, the person changes the
    kind and binds and unbinds code too. Every verdict's reopen gives back the
    exact state before it, code sits only on live headings, and a rebuild and a
    fold give the same store. Rejecting the rest in any order, when only plans
    and their rejects touched the heading, gives back its kind, level and code."""
    from codoc.calculus.doc import BULLET
    for trunk in (False, True):
        rng, clock = random.Random(seed), Clock()
        m, s = Mint(f"k{seed}", clock=clock), Store()
        heading(m, s, "h1", "Root")
        heading(m, s, "p1", "Two", level=2, after="h1")
        for sym in "ab":
            apply(s, m.op(K.BIND_ATTACH, {"fid": "p1"}, {"symbol": sym}))
        b = s.blocks["p1"]
        code = lambda: (b.kind, b.level, sorted((k, v.feature) for k, v in s.bindings.items()))   # noqa: E731
        start, touched = code(), False

        def decide(kind, pid):
            before = copy.deepcopy(s.state_fingerprint())
            done = apply(s, m.op(kind, {"pid": pid}, {}))
            assert done.conflict is None
            probe = replay(s)
            (back,) = invert(probe, Mint("u", "human:a", clock), done)
            assert back.conflict is None and probe.state_fingerprint() == before, (seed, trunk, kind)

        for step in range(rng.randint(3, 16)):
            r = rng.random()
            if not s.open_plans or r < 0.15:
                apply(s, m.op(K.PLAN_OPEN, {"pid": f"P{step}"}, {"kind": "edit", "target": "p1"}, actor=AGENT))
                continue
            pid = rng.choice(sorted(s.open_plans))
            if r < 0.55:
                pl = {"value": rng.choice([HEADING, PARAGRAPH, BULLET, HEADING])}
                if rng.random() < 0.5:
                    pl["level"] = rng.randint(1, 3)
                apply(s, m.op(K.BLOCK_SET, {"bid": "p1", "path": "kind"}, pl, actor=AGENT, plan=pid))
            elif r < 0.65:
                if b.kind == HEADING:
                    apply(s, m.op(K.BIND_ATTACH, {"fid": "p1"}, {"symbol": rng.choice("abc")}, actor=AGENT, plan=pid))
            elif trunk and r < 0.75:
                touched, q, sym = True, rng.random(), rng.choice("abc")
                if q < 0.4:
                    apply(s, m.op(K.BLOCK_SET, {"bid": "p1", "path": "kind"}, {"value": rng.choice([HEADING, PARAGRAPH])}))
                elif q < 0.7:
                    apply(s, m.op(K.BIND_ATTACH, {"fid": rng.choice(["h1"] + (["p1"] if b.kind == HEADING else []))},
                                  {"symbol": sym}))
                elif sym in s.bindings:
                    apply(s, m.op(K.BIND_DETACH, {"fid": s.bindings[sym].feature}, {"symbol": sym}))
            else:
                decide(K.PLAN_REJECT if rng.random() < 0.6 else K.PLAN_ACCEPT, pid)
            assert _code_on_live_headings(s), (seed, trunk, step)
        for pid in rng.sample(sorted(s.open_plans), len(s.open_plans)):
            decide(K.PLAN_REJECT, pid)
            assert _code_on_live_headings(s), (seed, trunk)
        if not touched and not any(p.status == "accepted" for p in s.plans.values()):
            assert code() == start, (seed, trunk, code(), start)
        assert replay(s).state_fingerprint() == s.state_fingerprint(), (seed, trunk)
        f = Store()
        for d in snapshot_ops(state_at(s, settled_prefix(s)), "snap", clock):
            assert replay_recorded(f, Op.from_json(d)).conflict is None
        for o in s.ops[settled_prefix(s):]:
            replay_recorded(f, Op.from_json(copy.deepcopy(o.to_json())))
        assert f.state_fingerprint() == s.state_fingerprint(), (seed, trunk)
