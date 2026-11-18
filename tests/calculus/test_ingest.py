"""The inline Markdown round trip, and `ingest`: new text for a field becomes text ops."""
import random

from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.ingest import ingest, tokenize
from codoc.calculus.inline_md import parse, render
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD, CITE, LINK
from tests.helpers import Mint, apply_mark, heading, md_of, paragraph, run_ingest


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


def test_one_thai_word_changed_re_authors_that_word_only():
    """Thai is unspaced, as `doclang` knows: the script table both read cuts it
    per character, so a loop's rewrite of one word leaves the sentence the
    person's."""
    m, s = Mint(), Store()
    heading(m, s, "h1", "Client")
    paragraph(m, s, "p1", "ส่งคำขอสามครั้ง", after="h1")         # "send the request three times"
    res, applied = run_ingest(s, Mint("l", "loop"), "p1", "ส่งคำขอสี่ครั้ง")  # three -> four
    assert res.refused is None and all(o.conflict is None for o in applied)
    t = s.blocks["p1"].text()
    assert t.live_text() == "ส่งคำขอสี่ครั้ง"
    assert len("".join(g.chars for g in t.segs if g.live and g.actor == "loop")) <= 2


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


def test_ingest_turns_a_bold_off_widens_one_and_appends_without_reauthoring():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    ins = paragraph(m, s, "p1", "alpha beta", after="h1")
    apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins.id, 0], "end": [ins.id, 4], "on": True}))
    res, _ = run_ingest(s, Mint("h", "human:a"), "p1", "alpha **beta**", actor="human:a")
    assert res.refused is None and md_of(s, "p1") == "alpha **beta**"
    m2, s2 = Mint(), Store()
    heading(m2, s2, "h1", "C")
    ins2 = paragraph(m2, s2, "p1", "Retries once via x.", after="h1")
    apply(s2, m2.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins2.id, 8], "end": [ins2.id, 11], "on": True}))
    res, _ = run_ingest(s2, Mint("h", "human:a"), "p1", "**Retries once** via x.", actor="human:a")
    assert md_of(s2, "p1") == "**Retries once** via x."
    assert s2.blocks["p1"].text().marks[0].actor == "human:a"
    m3, s3 = Mint(), Store()
    heading(m3, s3, "h1", "C")
    ins3 = paragraph(m3, s3, "p1", "Retries once", after="h1")
    apply(s3, m3.op(K.TEXT_MARK, {"bid": "p1", "path": "text"}, {"kind": BOLD, "attrs": {}, "start": [ins3.id, 8], "end": [ins3.id, 11], "on": True}))
    res, _ = run_ingest(s3, Mint("l", "loop"), "p1", "Retries **once** more")
    assert res.refused is None and res.deleted == 0 and md_of(s3, "p1") == "Retries **once** more"
    assert [(seg.actor, seg.chars) for seg in s3.blocks["p1"].text().segs if seg.live] == [("human:a", "Retries once"), ("loop", " more")]


def test_ingest_is_a_fixed_point_on_text_and_marks_fuzzed():
    rng = random.Random(11)
    words = ["alpha", "beta", "gamma", "delta", "once", "twice", "client", "retries"]
    seen_ok = 0
    for trial in range(200):
        m, s = Mint(), Store()
        heading(m, s, "h1", "T")
        base_words = [rng.choice(words) for _ in range(rng.randint(1, 8))]
        ins = paragraph(m, s, "p1", " ".join(base_words), after="h1")
        t = s.blocks["p1"].text()
        lt = t.live_text()
        cited = None
        for kind, attrs in ((BOLD, {}), (CITE, {"file": "c.py", "symbol": "C"})):
            w = rng.choice(base_words)
            lo = lt.index(w)
            apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                          {"kind": kind, "attrs": attrs, "start": list(t.ref_at(lo)), "end": list(t.ref_at(lo + len(w) - 1)), "on": True}))
            if kind == CITE:
                cited = w
        new_words = [w for w in base_words if rng.random() > 0.3] + [rng.choice(words) for _ in range(rng.randint(0, 3))]
        rng.shuffle(new_words)
        actor = rng.choice(["human:a", "loop"])
        parts = []
        for w in new_words:
            if w == cited and (actor == "loop" or rng.random() < 0.5):
                parts.append(f"[{w}](codoc:c.py#C)")
            elif rng.random() < 0.3:
                parts.append(f"**{w}**")
            else:
                parts.append(w)
        new_md = " ".join(parts) or "x"
        res, applied = run_ingest(s, Mint("i", actor), "p1", new_md, actor=actor)
        if res.refused:
            assert actor == "loop" and res.refused.startswith("would_drop_human_"), (trial, res.refused, new_md)
            continue
        assert all(o.conflict is None for o in applied), (trial, new_md, [(o.kind, o.conflict) for o in applied])
        t2 = s.blocks["p1"].text()
        assert md_of(s, "p1") == new_md, (trial, base_words, new_md, md_of(s, "p1"))
        for seg in t2.segs:
            if seg.live and seg.op == ins.id:
                assert seg.chars in " ".join(base_words)
        seen_ok += 1
    assert seen_ok > 100


def test_insert_inside_a_live_mark_is_not_captured_by_it():
    for kind, attrs, new in ((BOLD, {}, "**alpha** gamma **beta**"),
                             (LINK, {"href": "https://u"}, "[alpha](https://u) gamma [beta](https://u)")):
        m, s = Mint(), Store()
        heading(m, s, "h1", "C")
        ins = paragraph(m, s, "p1", "alpha beta", after="h1")
        apply_mark(m, s, "p1", ins, 0, 9, kind, attrs)
        res, applied = run_ingest(s, Mint("h", "human:a"), "p1", new, actor="human:a")
        assert res.refused is None and all(o.conflict is None for o in applied)
        assert md_of(s, "p1") == new


def test_append_after_a_bold_whose_end_char_is_dead_is_not_bolded():
    m, s = Mint(), Store()
    heading(m, s, "h1", "C")
    ins = paragraph(m, s, "p1", "alpha beta", after="h1")
    apply_mark(m, s, "p1", ins, 0, 9)
    apply(s, m.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"}, {"from": [ins.id, 5], "to": [ins.id, 9]}))
    assert md_of(s, "p1") == "**alpha**"
    run_ingest(s, Mint("l", "loop"), "p1", "**alpha** gamma")
    assert md_of(s, "p1") == "**alpha** gamma"


def test_ingest_fixed_point_with_multi_word_marks_and_inner_inserts_fuzzed():
    rng = random.Random(7)
    words = ["alpha", "beta", "gamma", "delta", "once", "twice"]
    for trial in range(300):
        m, s = Mint(), Store()
        heading(m, s, "h1", "T")
        base = [rng.choice(words) for _ in range(rng.randint(2, 6))]
        paragraph(m, s, "p1", " ".join(base), after="h1")
        t = s.blocks["p1"].text()
        lt = t.live_text()
        kind, attrs = rng.choice([(BOLD, {}), (LINK, {"href": "https://u"}), (CITE, {"file": "c.py", "symbol": "C"})])
        i, j = sorted(rng.sample(range(len(base)), 2))
        lo = lt.index(base[i]) if i == 0 else len(" ".join(base[:i])) + 1
        hi = len(" ".join(base[:j + 1])) - 1
        apply(s, m.op(K.TEXT_MARK, {"bid": "p1", "path": "text"},
                      {"kind": kind, "attrs": attrs, "start": list(t.ref_at(lo)), "end": list(t.ref_at(hi)), "on": True}))
        marked = set(range(i, j + 1))
        if rng.random() < 0.4:                        # a dead tail on the mark
            apply(s, m.op(K.TEXT_DELETE, {"bid": "p1", "path": "text"},
                          {"from": list(t.ref_at(hi - len(base[j]) + 1)), "to": list(t.ref_at(hi))}))
            base_now = base[:j] + base[j + 1:]
            marked = {k for k in marked if k != j}
            marked = {k if k < j else k - 1 for k in marked}
        else:
            base_now = list(base)
        k = rng.randint(0, len(base_now))
        new_words = base_now[:k] + [rng.choice(words)] + base_now[k:]
        wrap = lambda w, on: (f"**{w}**" if kind == BOLD else f"[{w}](https://u)" if kind == LINK else f"[{w}](codoc:c.py#C)") if on else w
        parts = []
        for idx, w in enumerate(new_words):
            orig = idx if idx < k else idx - 1
            on = idx != k and orig in marked and rng.random() < 0.9
            parts.append(wrap(w, on))
        new_md = " ".join(parts)
        res, applied = run_ingest(s, Mint("i", "human:a"), "p1", new_md, actor="human:a")
        assert res.refused is None and all(o.conflict is None for o in applied), (trial, new_md, [o.conflict for o in applied])
        got = md_of(s, "p1")
        # Adjacent same-kind spans render merged; compare on the parsed form.
        assert parse(got) == parse(new_md), (trial, base, new_md, got)
