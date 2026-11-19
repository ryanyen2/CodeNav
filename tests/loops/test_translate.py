"""Translate: per-block ingest, and a plan for a person's prose, a held feature or an open proposal."""

from codoc.calculus import ops as K
from codoc.calculus.derive import description, title
from codoc.doclang import resolve
from codoc.index.graph import Graph
from codoc.loops.bootstrap import bootstrap
from codoc.loops.translate import translate_tree
from codoc.workspace import Workspace
from tests.helpers import ROWS, fake_file, seed, seed_loop


def test_translate_ingests_per_block_and_plans_human_prose(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    bootstrap(ws, ROWS[:2], Graph(), propose_file=fake_file, propose_org=lambda *a, **k: [],
              propose_the_brief=lambda *a, **k: {}, concurrency=1)
    client = next(h.id for h in ws.store.headings() if title(ws.store, h.id) == "HTTP client")
    # The person rewrites one paragraph, so that block is human-written.
    body = ws.store.body(client)
    p2 = body[2]
    t = p2.text()
    ws.submit([{"kind": "text.delete", "target": {"bid": p2.id, "path": "text"}, "payload": {"from": list(t.ref_at(0)), "to": list(t.ref_at(6))}},
               {"kind": "text.insert", "target": {"bid": p2.id, "path": "text"}, "payload": {"after": "START", "text": "Repeats"}}],
              actor="human:local")
    zh = resolve("zh-Hans")

    def fake(features, language, **kw):
        def zh_text(text):
            if "once" in text:
                return "超时后重试 **once**。"
            return "发送路径。" if "send" in text.lower() else "发送请求。"
        return {f["id"]: ("HTTP 客户端" if f["title"] == "HTTP client" else "发送",
                          {b["id"]: zh_text(b["text"]) for b in f["blocks"]}) for f in features}

    res = translate_tree(ws, tmp_path / ".codoc", language=zh, propose=fake)
    assert res.translated == 2 and res.planned == 1 and not res.skipped, res.skipped
    # The human-written feature's translation is a plan (gray), not a trunk write:
    # the live view shows both wordings, the accepted view the translation alone.
    assert title(ws.store, client, "accepted") == "HTTP 客户端" and "client" in title(ws.store, client)
    assert any(p.actor == "translator:zh-Hans" and p.status == "open" for p in ws.store.plans.values())
    # Focus span survived by identity.
    assert "**once**" in description(ws.store, client, "accepted")
    # Idempotent: a re-run finds nothing to do while the plan is open.
    res2 = translate_tree(ws, tmp_path / ".codoc", language=zh, propose=fake)
    assert res2.translated == 0


def test_translating_a_queued_feature_is_a_plan_even_when_the_loop_wrote_it(tmp_path):
    """Translate goes through the machine gate like every machine write:
    while a feature has pending intent the document wins, so its translation
    is a plan even when the loop wrote every word of it."""
    from codoc.calculus import ops as K
    ws = Workspace(tmp_path / ".codoc")
    bootstrap(ws, ROWS[:2], Graph(), propose_file=fake_file, propose_org=lambda *a, **k: [],
              propose_the_brief=lambda *a, **k: {}, concurrency=1)
    client = next(h.id for h in ws.store.headings() if title(ws.store, h.id) == "HTTP client")
    ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": client}, "payload": {"asked": "wire the retry"}}],
              actor="human:local")                       # queued, and every word of it is the loop's

    def fake(features, language, **kw):
        return {f["id"]: ("译", {b["id"]: "译文。" for b in f["blocks"]}) for f in features}

    res = translate_tree(ws, tmp_path / ".codoc", language=resolve("zh-Hans"), propose=fake)
    assert res.translated == 2 and res.planned == 1, res           # only the queued one waits
    assert title(ws.store, client) != "译" and title(ws.store, client, "accepted") == "译"
    other = next(h.id for h in ws.store.headings() if h.id != client and "译" in title(ws.store, h.id, "accepted"))
    assert title(ws.store, other) == "译"                          # nothing pending there: the trunk


def test_translate_says_when_it_skipped_a_feature_for_an_open_plan(tmp_path):
    """A feature left alone because a proposal is open on it is reported as
    skipped, with why; counting it as already translated would be a lie in the
    one line the person reads."""
    from codoc.calculus import ops as K
    from codoc.calculus.ids import START
    from codoc.doclang import resolve

    ws = Workspace(tmp_path / ".codoc")
    ws.submit([
        {"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": "heading", "level": 1, "after": START}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "h1", "path": "text"}, "payload": {"after": START, "text": "Client"}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": "paragraph", "after": "h1"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "Retries once."}},
    ], actor="loop")
    pid = ws.open_plan(actor="loop", kind="edit", target="h1")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " Maybe."}}], actor="loop", plan=pid)
    res = translate_tree(ws, tmp_path / ".codoc", language=resolve("zh-Hans"),
                         propose=lambda payload, lang, **kw: {p["id"]: ("客户端", {b["id"]: "重试一次。" for b in p["blocks"]})
                                                              for p in payload})
    assert res.translated == 0 and res.already == 0
    assert [s.feature_id for s in res.skipped] == ["h1"] and "plan is open" in res.skipped[0].reason
    assert "1 skipped" in res.summary()


def test_translate_skips_a_feature_with_an_open_plan(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    pid = ws.open_plan(actor="loop", kind="edit", target="h1")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Maybe."}}], actor="loop", plan=pid)
    propose = lambda payload, lang, **kw: {p["id"]: ("客户端", {b["id"]: "放弃前重试一次。" for b in p["blocks"]})
                                          for p in payload}
    res = translate_tree(ws, tmp_path / ".codoc", language=resolve("zh-Hans"), propose=propose)
    assert res.translated == 0
    assert all(s.live for s in ws.store.blocks["p1"].text().segs if s.plan == pid)


def test_a_translation_writes_nothing_when_a_block_holds_an_open_plans_words(tmp_path):
    """The translator was shown the text without the plan's words. Its
    translation of that block is refused, and so is the rest of the feature."""
    from codoc.loops.translate import TranslateResult, _write
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    pid = ws.open_plan(actor="agent:claude-code", kind="edit", target="p1")
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(t.ref_at(len("Retries once") - 1)), "text": " with backoff"}}],
              actor="agent:claude-code", plan=pid)
    why = _write(ws, "h1", "Cliente", {"p1": "Reintenta una vez antes de rendirse."}, "translator:es",
                 resolve("es"), TranslateResult())
    assert why == "p1: open_plan"
    assert title(ws.store, "h1") == "Client"
    assert ws.store.blocks["p1"].text().live_text() == "Retries once with backoff before giving up."
    ws.close()


def test_translate_never_leaves_an_empty_open_plan(tmp_path):
    """A human-written feature whose translation is refused (a block came
    back as a different kind) must not be left with an open, empty plan the
    editor offers for a verdict."""
    from codoc.loops.translate import translate_tree
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)     # human-written
    from codoc.doclang import resolve
    lang = resolve("zh-Hans")

    def propose(payload, lang, **kw):
        # a bullet where the feature has a paragraph
        return {p["id"]: ("客户端", {b["id"]: "- 重试一次。" for b in p["blocks"]}) for p in payload}

    res = translate_tree(ws, cd, language=lang, propose=propose)
    assert res.skipped and "different kind" in res.skipped[0].reason
    open_ = [p for p in ws.store.plans.values() if p.status == "open"]
    assert not open_, [(p.id, p.ops) for p in open_]
    ws.close()
