"""Bootstrap, translate, snapshot and the v1 import on the calculus."""
import sqlite3

from codoc.calculus.derive import description, title, tree
from codoc.calculus.doc import fold_tree
from codoc.doclang import resolve
from codoc.graph.graph import Graph
from codoc.loops.bootstrap import bootstrap
from codoc.loops.translate import translate_tree
from codoc.pipelines.indexing.reader import ChunkRow
from codoc.substrate import log as L
from codoc.substrate.import_v1 import import_v1
from codoc.substrate.snapshot import write_snapshot
from codoc.substrate.workspace import Workspace


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
        out = {}
        for f in features:
            paras = f["description"].split("\n\n")
            zh_paras = []
            for p in paras:
                if "once" in p:
                    zh_paras.append("超时后重试 **once**。")
                elif "send" in p.lower():
                    zh_paras.append("发送路径。")
                else:
                    zh_paras.append("发送请求。")
            out[f["id"]] = ("HTTP 客户端" if f["title"] == "HTTP client" else "发送", "\n\n".join(zh_paras))
        return out

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


def test_snapshot_folds_and_rebuilds_identically(tmp_path):
    """A fold is the state, re-expressed: blocks, bindings, plans with their
    verdicts, and the watermarks (with what the person asked) all survive it."""
    from codoc.calculus import ops as K
    from codoc.calculus.derive import directive, phase
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
    assert list((tmp_path / ".codoc" / "log" / L.ARCHIVE_DIR).glob("*.jsonl")), "the folded logs were not archived"
    ws2.close()
    ws3 = Workspace(tmp_path / ".codoc")
    assert not ws3.problems
    assert ws3.store.state_fingerprint() == before
    assert phase(ws3.store, fid) == before_phase
    assert "make it retry twice" in directive(ws3.store, fid) == before_directive
    assert all(o.conflict is None for o in ws3.store.ops), [o.conflict for o in ws3.store.ops if o.conflict]


def test_import_v1_writes_one_batch_with_writer_roles(tmp_path):
    db = tmp_path / "codoc.db"
    con = sqlite3.connect(db)
    con.executescript("""
    CREATE TABLE features (id TEXT PRIMARY KEY, title TEXT, description TEXT, parent_id TEXT, rank TEXT,
                           lifecycle TEXT, created_at TEXT);
    CREATE TABLE bindings (feature_id TEXT, file TEXT, symbol_path TEXT, fingerprint TEXT, types_hash TEXT);
    CREATE TABLE feature_writers (feature_id TEXT PRIMARY KEY, writer TEXT, role TEXT, at TEXT);
    INSERT INTO features VALUES ('f-1','Top','First para.\n\nSecond para.',NULL,'V','active','1');
    INSERT INTO features VALUES ('f-2','Child','Body.','f-1','V','active','2');
    INSERT INTO features VALUES ('f-3','Gone','x','f-1','W','retired','3');
    INSERT INTO bindings VALUES ('f-2','a.py','a.py::f','fp','th');
    INSERT INTO feature_writers VALUES ('f-1','ryan','human','1');
    """)
    con.commit(); con.close()
    ws = Workspace(tmp_path / ".codoc")
    res = import_v1(ws, db)
    assert res["features"] == 2 and res["bindings"] == 1
    roots = fold_tree(ws.store.headings())
    assert title(ws.store, roots[0].id) == "Top" and [title(ws.store, c.id) for c in roots[0].children] == ["Child"]
    assert description(ws.store, roots[0].id) == "First para.\n\nSecond para."
    assert all(s.actor == "human:import" for s in ws.store.blocks[roots[0].id].text().segs)
    assert all(s.actor == "loop" for s in ws.store.blocks[roots[0].children[0].id].text().segs)


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
                         propose=lambda payload, lang, **kw: {p["id"]: ("客户端", "重试一次。") for p in payload})
    assert res.translated == 0 and res.already == 0
    assert [s.feature_id for s in res.skipped] == ["h1"] and "plan is open" in res.skipped[0].reason
    assert "1 skipped" in res.summary()
