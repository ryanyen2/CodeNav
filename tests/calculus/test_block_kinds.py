"""Rules for the block kinds: what converts, splits and merges, and how list levels move."""
from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.derive import markdown
from codoc.calculus.doc import BULLET, CODE_BLOCK, HEADING, NUMBERED, PARAGRAPH, TABLE
from codoc.calculus.ids import START
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD
from codoc.calculus.window import history_window
from tests.helpers import Mint, paragraph


def block(m: Mint, s: Store, bid: str, kind: str, text: str, after=START, level=None, path="text"):
    payload = {"kind": kind, "after": after, **({"level": level} if level else {})}
    apply(s, m.op(K.BLOCK_ADD, {"bid": bid}, payload))
    return apply(s, m.op(K.TEXT_INSERT, {"bid": bid, "path": path}, {"after": START, "text": text}))


def test_list_items_carry_a_level_through_conversion_and_split():
    m, s = Mint(), Store()
    ins = block(m, s, "b1", BULLET, "first second", level=3)
    assert s.blocks["b1"].level == 3
    apply(s, m.op(K.BLOCK_SET, {"bid": "b1", "path": "kind"}, {"value": NUMBERED}))
    assert (s.blocks["b1"].kind, s.blocks["b1"].level) == (NUMBERED, 3)          # bullet <-> numbered keeps depth
    split = apply(s, m.op(K.BLOCK_SPLIT, {"bid": "b1", "path": "text"}, {"at": [ins.id, 5], "new": "b2"}))
    assert split.conflict is None
    assert (s.blocks["b2"].kind, s.blocks["b2"].level) == (NUMBERED, 3)          # the tail is an item at the same depth
    apply(s, m.op(K.BLOCK_SET, {"bid": "b1", "path": "kind"}, {"value": PARAGRAPH}))
    assert s.blocks["b1"].level == 0
    assert apply(s, m.op(K.BLOCK_SET, {"bid": "b2", "path": "level"}, {"value": 9})).conflict is None
    assert s.blocks["b2"].level == 6
    assert apply(s, m.op(K.BLOCK_SET, {"bid": "b1", "path": "level"}, {"value": 2})).conflict == "no_level"


def test_source_blocks_do_not_convert_split_or_merge():
    m, s = Mint(), Store()
    paragraph(m, s, "p", "prose", after=START)
    ins = block(m, s, "c", CODE_BLOCK, "a = 1\nb = 2", after="p", path="source")
    block(m, s, "t", TABLE, "| a |\n|---|", after="c", path="source")
    assert apply(s, m.op(K.BLOCK_SET, {"bid": "c", "path": "kind"}, {"value": PARAGRAPH})).conflict == "kind_incompatible"
    assert apply(s, m.op(K.BLOCK_SET, {"bid": "p", "path": "kind"}, {"value": TABLE})).conflict == "kind_incompatible"
    split = apply(s, m.op(K.BLOCK_SPLIT, {"bid": "c", "path": "source"}, {"at": [ins.id, 4], "new": "c2"}))
    assert split.conflict == "not_splittable" and "c2" not in s.blocks
    assert apply(s, m.op(K.BLOCK_MERGE, {"into": "c", "bid": "t", "path": "source"}, {})).conflict == "kind_incompatible"
    assert apply(s, m.op(K.BLOCK_MERGE, {"into": "p", "bid": "c", "path": "text"}, {})).conflict == "kind_incompatible"
    assert apply(s, m.op(K.BLOCK_SET, {"bid": "p", "path": "kind"}, {"value": HEADING})).conflict is None


def test_widening_a_mark_keeps_its_placer_and_takes_the_new_clock():
    m, s = Mint(), Store()
    ins = paragraph(m, s, "p", "one two three", after=START)
    first = apply(s, m.op(K.TEXT_MARK, {"bid": "p", "path": "text"},
                          {"kind": BOLD, "attrs": {}, "start": [ins.id, 0], "end": [ins.id, 3], "on": True}, actor="human:a"))
    second = apply(s, m.op(K.TEXT_MARK, {"bid": "p", "path": "text"},
                           {"kind": BOLD, "attrs": {}, "start": [ins.id, 4], "end": [ins.id, 6], "on": True}, actor="loop"))
    (mark,) = s.blocks["p"].text().marks
    assert (mark.actor, mark.op, mark.at) == ("human:a", first.id, second.at)
    assert markdown(s.blocks["p"].text()) == "**one two** three"


def test_the_accepted_view_kills_what_accept_would():
    """A word typed inside a proposed deletion survives the verdict, so the
    `accepted` read mode keeps it too."""
    m, s = Mint(), Store()
    ins = paragraph(m, s, "p", "keep cut this", after=START)
    apply(s, m.op(K.PLAN_OPEN, {"pid": "p-1"}, {"kind": "edit", "target": None, "note": ""}, actor="loop"))
    apply(s, m.op(K.TEXT_MARK, {"bid": "p", "path": "text"},
                  {"kind": "suggest_del", "attrs": {}, "start": [ins.id, 5], "end": [ins.id, 12], "on": True},
                  actor="loop", plan="p-1"))
    typed = apply(s, m.op(K.TEXT_INSERT, {"bid": "p", "path": "text"}, {"after": [ins.id, 7], "text": " and"}))
    assert typed.conflict is None
    accepted = markdown(s.blocks["p"].text(), "accepted", s)
    apply(s, m.op(K.PLAN_ACCEPT, {"pid": "p-1"}, {}))
    assert accepted == s.blocks["p"].text().live_text() == "keep  and"


def test_the_timeline_reports_a_source_block():
    m, s = Mint(), Store()
    ins = block(m, s, "t", TABLE, "| a |\n|---|", path="source")
    apply(s, m.op(K.TEXT_INSERT, {"bid": "t", "path": "source"}, {"after": [ins.id, 10], "text": "\n| 1 |"}))
    w = history_window(s)
    assert w["ops"][-1]["blocks"] == {"t": "| a |\n|---|\n| 1 |"}
