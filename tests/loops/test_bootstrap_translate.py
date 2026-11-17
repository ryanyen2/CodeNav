"""Bootstrap, translate, snapshot and the v1 import on the calculus."""
import sqlite3

from codoc.calculus.derive import description, title, tree
from codoc.calculus.doc import fold_tree
from codoc.doclang import resolve
from codoc.index.graph import Graph
from codoc.loops.bootstrap import bootstrap
from codoc.loops.translate import translate_tree
from codoc.index.reader import ChunkRow
from codoc.workspace import log as L
from codoc.workspace.snapshot import write_snapshot
from codoc.workspace import Workspace


def row(file, sym, src="def f(): ...", th="t"):
    return ChunkRow(id=0, file=file, symbol_path=sym, language="python", source=src, tokens_hash=f"h-{sym}",
                    types_hash=th, start_byte=0, end_byte=1)


ROWS = [row("net/client.py", "net/client.py::Client"), row("net/client.py", "net/client.py::Client.send"),
        row("store/db.py", "store/db.py::open"), row("store/db.py", "store/db.py::_helper")]


def fake_file(file, chunks, edges, titles, **kw):
    if file == "net/client.py":
        return [{"id": "n1", "kind": "add_node", "title": "HTTP client",
                 "description": "Sends requests.\n\nRetries **once** on timeout.",
                 "bindings": [["net/client.py", "net/client.py::Client"]]},
                {"id": "n2", "kind": "add_node", "parent_id": "n1", "title": "Sending",
                 "description": "The send path.", "bindings": [["net/client.py", "net/client.py::Client.send"]]}]
    return [{"id": "n1", "kind": "add_node", "title": "Store", "description": "Opens the database.",
             "bindings": [["store/db.py", "store/db.py::open"], ["store/db.py", "store/db.py::nope"]]}]


def fake_org(features, edges, **kw):
    return [{"id": "t1", "kind": "add_node", "title": "Networking", "description": "Everything on the wire."},
            {"kind": "move_node", "feature_id": next(f["id"] for f in features if f["title"] == "HTTP client"), "parent_id": "t1"}]


def test_bootstrap_builds_a_tree_with_coverage_and_themes(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    res = bootstrap(ws, ROWS, Graph(), propose_file=fake_file, propose_org=fake_org,
                    propose_the_brief=lambda *a, **k: {}, concurrency=1)
    assert res.features == 4 and not res.skipped
    roots = fold_tree(ws.store.headings())
    titles = {r.id: title(ws.store, r.id) for r in roots}
    assert sorted(titles.values()) == ["Networking", "Store"]
    net = next(r for r in roots if titles[r.id] == "Networking")
    assert [title(ws.store, c.id) for c in net.children] == ["HTTP client"]
    assert [title(ws.store, g.id) for g in net.children[0].children] == ["Sending"]
    client = net.children[0].id
    assert description(ws.store, client) == "Sends requests.\n\nRetries **once** on timeout."
    # Coverage: the helper the model ignored is bound to the file's node; the invented symbol is dropped.
    owner = {b.symbol: b.feature for b in ws.store.bindings.values()}
    assert "store/db.py::_helper" in owner and "store/db.py::nope" not in owner
    assert owner["store/db.py::_helper"] == owner["store/db.py::open"]
    # Every segment is the loop's.
    assert all(s.actor == "loop" for b in ws.store.ordered() for s in b.text().segs)


def test_translate_ingests_per_block_and_plans_human_prose(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    bootstrap(ws, ROWS[:2], Graph(), propose_file=fake_file, propose_org=lambda *a, **k: [],
              propose_the_brief=lambda *a, **k: {}, concurrency=1)
    client = next(h.id for h in ws.store.headings() if title(ws.store, h.id) == "HTTP client")
    # The person rewrites one paragraph, so that block is human-written.
    body = ws.store.feature_body(client)
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
    """Review 7: translate had its own copy of the machine gate and the copies
    had drifted — it asked only whose prose this is. 01 §4 rule 8 holds for
    every machine write: while a feature has pending intent the document wins,
    so the translation waits for a verdict like anything else."""
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


def test_snapshot_folds_and_rebuilds_identically(tmp_path):
    """A fold is the state, re-expressed: blocks, bindings, plans with their
    verdicts, and the watermarks (with what the person asked) all survive it."""
    from codoc.calculus import ops as K
    from codoc.calculus.derive import phase
    from codoc.loops.realize import directive
    from codoc.loops.realize import handoff

    ws = Workspace(tmp_path / ".codoc")
    bootstrap(ws, ROWS, Graph(), propose_file=fake_file, propose_org=fake_org, propose_the_brief=lambda *a, **k: {}, concurrency=1)
    fid = ws.store.headings()[0].id
    body = ws.store.feature_body(fid)[-1]
    t = body.text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": body.id, "path": "text"},
                "payload": {"after": list(t.last_live_ref()), "text": " Mine."}}], actor="human:local")
    handoff(ws, fid, actor="human:local", asked="make it retry twice")
    kept = ws.open_plan(actor="loop", kind="edit", target=fid, note="a proposal nobody answered")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": body.id, "path": "text"},
                "payload": {"after": list(ws.store.blocks[body.id].text().last_live_ref()), "text": " Maybe."}}],
              actor="loop", plan=kept)
    gone = ws.open_plan(actor="loop", kind="edit", target=fid, note="answered")
    ws.submit([{"kind": K.PLAN_REJECT, "target": {"pid": gone}, "payload": {}}], actor="human:local")
    ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": fid}, "payload": {}}], actor="human:local")
    before, before_phase, before_directive = ws.store.state_fingerprint(), phase(ws.store, fid), directive(ws.store, fid)
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    write_snapshot(tmp_path / ".codoc", ws2.store, ws2.clock)
    assert len(L.list_snapshots(tmp_path / ".codoc")) == 1
    assert all("archive" not in str(p) for p in L.list_sessions(tmp_path / ".codoc"))
    # An open proposal is work in flight, so the fold stops before it and the
    # session file holding it stays the log — the verdict still reads its ops.
    assert L.list_sessions(tmp_path / ".codoc"), "the log a verdict still needs was archived"
    ws2.close()
    ws3 = Workspace(tmp_path / ".codoc")
    assert not ws3.problems
    assert ws3.store.state_fingerprint() == before
    assert phase(ws3.store, fid) == before_phase
    assert "make it retry twice" in directive(ws3.store, fid) == before_directive
    assert all(o.conflict is None for o in ws3.store.ops), [o.conflict for o in ws3.store.ops if o.conflict]


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
