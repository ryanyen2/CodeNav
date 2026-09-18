"""inline_md round trip and the ingest boundary (spec §4)."""
from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.ids import START
from codoc.calculus.ingest import ingest, resolve_pending_refs, tokenize
from codoc.calculus.inline_md import Span, parse, render
from codoc.calculus.ops import Op
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD, CITE, LINK

from tests.calculus.test_apply import Mint, heading, paragraph


def test_inline_md_parse_and_render_fixed_point():
    md = "Retries **three times** via [the client](codoc:net/client.py#Client.send), see [docs](https://x.y/z)."
    text, spans = parse(md)
    assert text == "Retries three times via the client, see docs."
    assert [(s.kind, s.start, s.end) for s in spans] == [(BOLD, 8, 19), (CITE, 24, 34), (LINK, 40, 44)]
    assert spans[1].attrs == {"file": "net/client.py", "symbol": "Client.send"}
    assert render(text, spans) == md
    # Non-forms are plain text.
    assert parse("a * b [c] (d) **")[0] == "a * b [c] (d) **"


def test_tokenize_is_lossless_and_script_aware():
    s = "hello  world, 你好世界 ok"
    toks = tokenize(s)
    assert "".join(toks) == s
    assert "你" in toks and "hello" in toks and "  " in toks and toks[-1] == "ok"


def run_ingest(store: Store, m: Mint, bid: str, new_md: str, actor="loop", plan=None, base=None):
    t = store.blocks[bid].text()
    res = ingest(t, bid=bid, path="text", base_text=t.live_text(), new_md=new_md, actor=actor, trunk=plan is None)
    if res.refused:
        return res, []
    ops = []
    insert_ids = []
    for b in res.bodies:
        op = m.op(b["kind"], b["target"], b["payload"], actor=actor, plan=plan)
        if b["kind"] == K.TEXT_INSERT:
            insert_ids.append(op.id)
        ops.append(op)
    # Marks may reference inserts by ordinal; resolve then apply in order.
    bodies = [o.payload for o in ops]
    resolve_pending_refs([{"payload": p} for p in bodies], insert_ids)
    applied = [apply(store, o) for o in ops]
    return res, applied


def test_ingest_repairs_keep_human_words_and_attribute_new_ones_to_loop():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client")
    paragraph(m, s, "p1", "The client retries once and then gives up.", after="h1")
    res, applied = run_ingest(s, Mint("l", "loop"), "p1", "The client retries three times and then gives up.")
    assert res.refused is None and all(o.conflict is None for o in applied)
    t = s.blocks["p1"].text()
    assert t.live_text() == "The client retries three times and then gives up."
    authors = [(seg.actor, seg.chars) for seg in t.segs if seg.live]
    assert ("loop", "three times") in authors
    assert all(a == "human:a" for a, c in authors if c != "three times")
    assert 0 < res.deleted_fraction < 0.2


def test_ingest_refuses_a_stale_base_text():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client")
    paragraph(m, s, "p1", "alpha beta", after="h1")
    t = s.blocks["p1"].text()
    res = ingest(t, bid="p1", path="text", base_text="alpha", new_md="alpha gamma", actor="loop")
    assert res.refused == "stale_base"
    # The person typed after the loop read: the loop's text is stale by construction.
    apply(s, m.op(K.TEXT_INSERT, {"bid": "p1", "path": "text"}, {"after": list(t.last_live_ref()), "text": " delta"}))
    res = ingest(s.blocks["p1"].text(), bid="p1", path="text", base_text="alpha beta", new_md="alpha beta gamma", actor="loop")
    assert res.refused == "stale_base"


def test_ingest_keeps_human_marks_by_identity_and_refuses_dropping_them():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client")
    ins = paragraph(m, s, "p1", "Retries once via the client.", after="h1")
    # Human bolds "once" and cites "the client".
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                  {"kind": BOLD, "attrs": {}, "start": [ins.id, 8], "end": [ins.id, 11], "on": True}))
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                  {"kind": CITE, "attrs": {"file": "c.py", "symbol": "C"}, "start": [ins.id, 17], "end": [ins.id, 26], "on": True}))
    # Loop rephrases around them, keeping both forms in its markdown.
    res, applied = run_ingest(s, Mint("l", "loop"), "p1", "Now retries **once** via [the client](codoc:c.py#C) only.")
    assert res.refused is None
    t = s.blocks["p1"].text()
    assert t.live_text() == "Now retries once via the client only."
    kinds = sorted((mk.kind, mk.actor) for mk in t.marks)
    assert kinds == [(BOLD, "human:a"), (CITE, "human:a")]   # kept, not re-authored
    # A loop write that drops the human bold entirely is refused.
    res2, _ = run_ingest(s, Mint("l2", "loop"), "p1", "Now retries via the client only.")
    assert res2.refused == "would_drop_human_bold"
    # A new link the loop adds is minted with the loop as its actor.
    res3, applied3 = run_ingest(s, Mint("l3", "loop"), "p1",
                                "Now retries **once** via [the client](codoc:c.py#C) only, see [rfc](https://r.fc).")
    assert res3.refused is None and all(o.conflict is None for o in applied3)
    t = s.blocks["p1"].text()
    assert any(mk.kind == LINK and mk.actor == "loop" for mk in t.marks)


def test_ingest_refuses_paragraph_breaks_and_full_rewrite_is_measured():
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client")
    paragraph(m, s, "p1", "one two three", after="h1")
    t = s.blocks["p1"].text()
    res = ingest(t, bid="p1", path="text", base_text=t.live_text(), new_md="one\n\ntwo", actor="loop")
    assert res.refused == "multi_paragraph"
    res = ingest(t, bid="p1", path="text", base_text=t.live_text(), new_md="entirely different words", actor="loop")
    assert res.deleted_fraction == 1.0   # whitespace never survives on its own
