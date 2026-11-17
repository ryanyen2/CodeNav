"""Rules for the block kinds: what converts, splits and merges, and how list levels move."""
import copy

from codoc.calculus import block_md, doc
from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.block_md import BlockSpec
from codoc.calculus.derive import block_entry, block_spec, markdown
from codoc.calculus.doc import BUILTIN, BULLET, CODE_BLOCK, HEADING, NUMBERED, PARAGRAPH, TABLE, Layout
from codoc.calculus.export import export_markdown
from codoc.calculus.ids import START
from codoc.calculus.ingest import block_bodies, resolve_pending_refs
from codoc.calculus.ops import Op
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD
from codoc.calculus.window import history_window
from tests.helpers import Mint, paragraph, replay


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


def test_a_newline_is_content_in_a_source_block_and_refused_in_a_line():
    m, s = Mint(), Store()
    ins = paragraph(m, s, "p", "one", after=START)
    code = block(m, s, "c", CODE_BLOCK, "a = 1", after="p", path="source")
    refused = apply(s, m.op(K.TEXT_INSERT, {"bid": "p", "path": "text"}, {"after": [ins.id, 2], "text": "\ntwo"}))
    assert refused.conflict == "newline_in_line" and s.blocks["p"].text().live_text() == "one"
    kept = apply(s, m.op(K.TEXT_INSERT, {"bid": "c", "path": "source"}, {"after": [code.id, 4], "text": "\nb = 2"}))
    assert kept.conflict is None and s.blocks["c"].text("source").live_text() == "a = 1\nb = 2"


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
    assert w["ops"][-1]["blocks"] == {"t": {"source": "| a |\n|---|\n| 1 |"}}


WIDGET = Layout(("alt", "caption"))


def _widget_log(monkeypatch) -> list[Op]:
    """The ops a writer that has the `widget` kind sends to add one with words."""
    monkeypatch.setitem(doc._REGISTERED, "widget", WIDGET)
    bodies, _ = block_bodies([BlockSpec("widget", content={"url": "https://x.org", "caption": "A **plot**"})],
                             after=START)
    m, inserts, out = Mint(), [], []
    for b in bodies:
        payload = dict(b["payload"])
        resolve_pending_refs(payload, inserts)
        out.append(m.op(b["kind"], b["target"], payload))
        if b["kind"] == K.TEXT_INSERT:
            inserts.append(out[-1].id)
    return out


def _submit(s: Store, log: list[Op]) -> None:
    for o in log:
        got = apply(s, Op.from_json(copy.deepcopy(o.to_json())))
        assert got.conflict is None, (o.kind, got.conflict)


def test_a_clone_without_a_plugin_kind_replays_its_text_the_same(monkeypatch):
    """The layout rides on the block.add, so a clone that lacks the kind still
    reads its Text fields as Text: the same log ends in the same state (I6)."""
    log = _widget_log(monkeypatch)
    with_kind = Store()
    _submit(with_kind, log)
    monkeypatch.delitem(doc._REGISTERED, "widget")
    without = Store()
    _submit(without, log)
    assert without.state_fingerprint() == with_kind.state_fingerprint()
    entry = block_entry(without, without.blocks[log[0].target["bid"]])
    assert entry["layout"] == WIDGET.to_json() and entry["fields"]["caption"]["text"] == "A plot"
    # An add that carries no layout (an old writer) takes this process's
    # registry and records it, so a replay without the kind reads the same.
    monkeypatch.setitem(doc._REGISTERED, "widget", WIDGET)
    m, old = Mint(), Store()
    apply(old, m.op(K.BLOCK_ADD, {"bid": "w"}, {"kind": "widget", "after": START}))
    apply(old, m.op(K.TEXT_INSERT, {"bid": "w", "path": "caption"}, {"after": START, "text": "hi"}))
    monkeypatch.delitem(doc._REGISTERED, "widget")
    assert old.ops[0].payload["layout"] == {"text": ["alt", "caption"], "flow": True, "export": True}
    assert replay(old).state_fingerprint() == old.state_fingerprint()
    # The timeline reports every Text field, not only the first.
    assert history_window(old)["ops"][-1]["blocks"] == {"w": {"caption": "hi"}}


def test_a_builtin_kind_ignores_a_sent_layout():
    m, s = Mint(), Store()
    add = apply(s, m.op(K.BLOCK_ADD, {"bid": "p"}, {"kind": PARAGRAPH, "after": START,
                                                    "layout": {"text": ["note"], "flow": False}}))
    assert add.conflict is None and "layout" not in add.payload and s.blocks["p"].layout == BUILTIN[PARAGRAPH]
    assert apply(s, m.op(K.TEXT_INSERT, {"bid": "p", "path": "note"}, {"after": START, "text": "x"})).conflict == "not_text"
    for bad in (["alt"], {"text": "alt"}, {"text": ["$layout"]}):
        assert apply(s, m.op(K.BLOCK_ADD, {"bid": "w"}, {"kind": "widget", "layout": bad})).conflict == "malformed"
    # `$layout` is the fence's own key, so no field may take that name.
    assert apply(s, m.op(K.BLOCK_ADD, {"bid": "w"}, {"kind": "widget", "content": {"$layout": 1}})).conflict == "reserved_path"
    apply(s, m.op(K.BLOCK_ADD, {"bid": "w2"}, {"kind": "widget", "layout": {"text": ["alt"]}}))
    assert apply(s, m.op(K.BLOCK_SET, {"bid": "w2", "path": "$layout"}, {"value": 1})).conflict == "reserved_path"


def test_the_export_of_a_kind_without_code_is_a_fence_that_parses_back(monkeypatch):
    log = _widget_log(monkeypatch)
    monkeypatch.delitem(doc._REGISTERED, "widget")
    s = Store()
    _submit(s, log)
    md = export_markdown(s)
    assert md.startswith("```codoc:widget"), md
    (back,) = block_md.parse(md)
    assert back == block_spec(s, s.blocks[back.id], "rejected")
    assert back.content == {"$layout": WIDGET.to_json(), "url": "https://x.org", "caption": "A **plot**"}
    # A paste on this side, which has no code for the kind, rebuilds the same block.
    bodies, (bid,) = block_bodies([back], after=START)
    pasted, m = Store(), Mint("p")
    _submit(pasted, [m.op(b["kind"], b["target"], b["payload"]) for b in bodies[:2]])
    assert pasted.blocks[bid].layout == WIDGET and pasted.blocks[bid].content["url"] == "https://x.org"
    assert pasted.blocks[bid].text("caption").live_text() == "A plot"
