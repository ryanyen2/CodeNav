"""Block kinds on the extension interface (`03-extensions.md`)."""
from codoc.calculus import ops as K
from codoc.calculus.derive import attribution, markdown
from codoc.loops.realize import directive
from codoc.calculus.doc import text_fields
from codoc.calculus.export import export_markdown
from codoc.calculus.ids import START
from codoc.index.graph import Graph
from codoc.kinds import LowerContext, get, plain
from codoc.kinds.builtins import diagram_lower, edges_of
from codoc.kinds.lift import lift_pass
from codoc.kinds.write import add_block, write_block
from codoc.loops.realize import handoff
from codoc.workspace import Workspace
from tests.loops.test_reflect import seed


def test_registration_declares_text_fields_and_schema_validation():
    assert text_fields("diagram") == text_fields("table") == text_fields("code") == ("source",)
    assert text_fields("image") == ("alt", "caption")
    img = get("image")
    assert img.validate({"asset": ".codoc/media/a.png", "alt": "x"}) is None
    assert img.validate({"alt": "x"}) == "asset is required"
    assert img.validate({"asset": "a", "source": "camera"}) == "source must be one of ['screenshot', 'upload', 'agent']"
    assert img.validate({"asset": "a", "bogus": 1}) == "unknown field 'bogus'"
    assert img.validate({"asset": "a", "alt": 3}) == "alt must be a string"


def test_add_and_write_block_attribute_text_fields_and_set_the_rest(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    w = add_block(ws, "image", {"asset": ".codoc/media/ui.png", "alt": "the login form", "caption": "Before the **fix**."},
                  after="p1", actor="human:local")
    assert w.ok and ws.store.blocks[w.bid].kind == "image"
    blk = ws.store.blocks[w.bid]
    assert blk.content["asset"] == ".codoc/media/ui.png" and markdown(blk.text("caption")) == "Before the **fix**."
    assert [r.actor for r in attribution(blk.text("alt"))] == ["human:local"]
    # An agent's write goes through the same boundary, as the agent — and the
    # feature holds the person's unsent ink, so the agent's words are a proposal
    # on the page rather than prose on the trunk (01 §4 rule 8).
    w2 = write_block(ws, w.bid, {"caption": "Before the **fix**, after login.", "source": "agent"}, actor="agent:claude-code")
    assert w2.ok
    assert blk.content["source"] == "agent"
    authors = {(r.actor, blk.text("caption").live_text()[r.start:r.end]) for r in attribution(blk.text("caption"))}
    assert ("agent:claude-code", ", after login") in authors and ("human:local", "Before the fix") in authors
    proposed = [p for p in ws.store.plans.values() if p.status == "open" and p.actor == "agent:claude-code"]
    assert len(proposed) == 1, [p.to_json() for p in ws.store.plans.values()]
    # Unknown fields and dead blocks are refused before anything is written.
    n = len(ws.store.ops)
    assert write_block(ws, w.bid, {"nope": 1}, actor="loop").refused == [("", "unknown field 'nope'")]
    assert len(ws.store.ops) == n
    # The export fences the block with its Text fields as Markdown; comments stay out.
    c = add_block(ws, "comment", {"thread": "t1", "anchor": "p1", "anchor_text": "Retries once", "body": "Really?"},
                  after=w.bid, actor="human:local")
    assert c.ok
    md = export_markdown(ws.store)
    assert "```codoc:image" in md and '"caption": "Before the **fix**."' in md and "Really?" not in md


def test_diagram_lifts_from_the_graph_and_lowers_an_edge_delta(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": "c.py::retry"}}], actor="loop")
    g = Graph([{"src_symbol": "c.py::send", "dst_name": "retry", "dst_symbol": "c.py::retry", "kind": "call", "src_file": "c.py", "internal": True}])
    d = add_block(ws, "diagram", {"source": "", "derived": True}, after="p1", actor="loop")
    assert lift_pass(ws, g) == 1
    src = ws.store.blocks[d.bid].text("source").live_text()
    assert "send --> retry" in src and lift_pass(ws, g) == 0          # idempotent
    # The person draws an edge: the directive names it; a freeform node asks instead.
    old = plain(ws.store.blocks[d.bid], ws.store)
    ctx = LowerContext("h1", [b for b in ws.store.bindings.values() if b.feature == "h1"])
    new = {**old, "source": old["source"] + "\n  retry --> log"}
    assert diagram_lower(old, new, ctx) == "Add a dependency or call from `retry` to `log` (the author drew that edge)."
    assert edges_of(new["source"]) == {("send", "retry"), ("retry", "log")}
    assert "Ask the author" in diagram_lower(old, {**old, "source": old["source"] + "\n  node[Label]"}, ctx)
    assert diagram_lower(old, old, ctx) is None
    # An authored diagram is explanation: the loop never redraws it and an edit to it asks for nothing.
    a = add_block(ws, "diagram", {"source": "sequenceDiagram\n  A->>B: hi"}, after=d.bid, actor="human:local")
    assert lift_pass(ws, g) == 0 and ws.store.blocks[a.bid].text("source").live_text().startswith("sequenceDiagram")
    assert diagram_lower({"source": "a --> b"}, {"source": "a --> c"}, ctx) is None
    # A held feature's diagram is not lifted over the person's edit.
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Mine."}}], actor="human:local")
    handoff(ws, "h1", actor="human:local")
    g2 = Graph([{"src_symbol": "c.py::send", "dst_name": "other", "dst_symbol": "c.py::other", "kind": "call", "src_file": "c.py", "internal": True}])
    assert lift_pass(ws, g2) == 0


def test_a_lift_lands_on_the_trunk_however_much_of_the_picture_changed(tmp_path):
    """Review 7: a lift "is attribution, like a binding refresh, never intent"
    (03 §5.1), so the machine gate in `Workspace.ingest` does not apply to it.
    It used to: when the code moved far enough, the re-derived diagram deleted
    more than half its source and the always-on channel stopped to ask."""
    def graph(*edges):
        return Graph([{"src_symbol": s, "dst_name": d.split("::")[-1], "dst_symbol": d,
                       "kind": "call", "src_file": "c.py", "internal": True} for s, d in edges])

    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    for sym in ("retry", "backoff", "jitter"):
        ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": f"c.py::{sym}"}}], actor="loop")
    d = add_block(ws, "diagram", {"source": "", "derived": True}, after="p1", actor="loop")
    assert d.ok and lift_pass(ws, graph()) == 1 and "jitter" in ws.store.blocks[d.bid].text("source").live_text()
    for sym in ("send", "retry", "backoff", "jitter"):                # every symbol it drew is gone
        ws.submit([{"kind": K.BIND_DETACH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": f"c.py::{sym}"}}], actor="loop")
    ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": "c.py::alpha"}}], actor="loop")
    assert lift_pass(ws, graph()) == 1
    assert [p.id for p in ws.store.plans.values() if p.status == "open"] == []
    src = ws.store.blocks[d.bid].text("source")
    assert markdown(src, "rejected", ws.store) == src.live_text() == "flowchart TB\n  alpha"


def test_an_accepted_plan_on_a_diagram_reaches_the_directive(tmp_path):
    """02 §3.3: "a plan is by definition a request to build". `_at_realized`
    holds back what the AUTHOR changed since the last realization, and
    accepting a plan is how an author adopts an agent's words — so the edge
    they accepted has to reach `lower` as theirs, not sit in `old` already."""
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": "c.py::retry"}}], actor="loop")
    d = add_block(ws, "diagram", {"source": "", "derived": True}, after="p1", actor="loop")
    assert d.ok and lift_pass(ws, Graph([])) == 1                     # no edges in the code yet
    base = ws.store.blocks[d.bid].text("source").live_text()
    pid = ws.open_plan(actor="agent:claude-code", kind="edit", target=d.bid, note="wire the retry")
    res, ops = ws.ingest(d.bid, "source", base_text=base, new_md=base + "\n  send --> retry",
                         actor="agent:claude-code", plan=pid)
    assert not res.refused and ops
    ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}},
               {"kind": K.FEATURE_HANDOFF, "target": {"fid": "h1"}, "payload": {"asked": f"Build the plan {pid}."}}],
              actor="human:local")
    out = directive(ws.store, "h1")
    assert "send --> retry" in out and "Add a dependency or call from `send` to `retry`" in out, out


def test_mcp_write_block_adds_and_edits_as_the_agent(tmp_path):
    from codoc.mcp import tools
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    ws.close()
    r = tools.write_block(str(cd), kind="diagram", feature_id="h1", content={"source": "flowchart TB\n  a[Start] --> b"})
    assert r["ok"] and r["ingested"] and all(i["refused"] is None for i in r["ingested"])
    st = Workspace(cd, readonly=True).store
    blk = st.blocks[r["bid"]]
    # a source field is raw: brackets and asterisks are text, not markup
    assert blk.kind == "diagram" and blk.text("source").live_text() == "flowchart TB\n  a[Start] --> b"
    assert blk.text("source").marks == []
    assert {s.actor for s in blk.text("source").segs} == {"agent:claude-code"}
    r2 = tools.write_block(str(cd), bid=r["bid"], content={"source": "flowchart TB\n  a[Start] --> c"})
    assert r2["ok"] and Workspace(cd, readonly=True).store.blocks[r["bid"]].text("source").live_text().endswith("--> c")
    assert tools.write_block(str(cd), bid=r["bid"], content={"nope": 1})["error"] == "unknown field 'nope'"
    assert "unknown kind" in tools.write_block(str(cd), kind="gif", feature_id="h1", content={})["error"]


def test_a_feature_carrying_media_survives_every_whole_feature_pass(tmp_path):
    """A block whose Text fields are not called `text` (a diagram, a comment)
    is in the body like any other: the author gate, the blame read, a delete
    plan and a translation all walk its own fields."""
    from codoc.calculus.derive import description, plan_claims
    from codoc.doclang import resolve
    from codoc.loops.context import block_written_by, written_by
    from codoc.loops.edits import strike_bodies
    from codoc.loops.translate import translate_tree
    from codoc.mcp import tools

    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    d = add_block(ws, "diagram", {"source": "flowchart TB\n  a --> b"}, after="p1", actor="loop")
    c = add_block(ws, "comment", {"thread": "t1", "anchor": "p1", "anchor_text": "once", "body": "Twice?"},
                  after=d.bid, actor="human:local")
    assert block_written_by(ws.store, d.bid) == "loop" and block_written_by(ws.store, c.bid) == "human"
    # The comment is a request about the prose, not the prose: the feature is
    # still machine-written, which is what the amend gate and translate read.
    assert written_by(ws.store, "h1") == "loop"
    # the description is the prose and the diagram; the comment is not part of what the feature says
    assert description(ws.store, "h1") == "Retries once before giving up.\n\n```mermaid\nflowchart TB\n  a --> b\n```"
    ws.close()
    blame = tools.read_history(str(tmp_path / ".codoc"), "h1")["attribution"]
    assert f"{d.bid}.source" in blame and f"{c.bid}.body" in blame and "p1" in blame
    ws = Workspace(tmp_path / ".codoc")
    struck = strike_bodies(ws.store, [b.id for b in ws.store.feature_body("h1")], "p-1")
    assert sorted(b["target"]["path"] for b in struck) == ["body", "source", "text", "text"]
    propose = lambda payload, lang, **kw: {p["id"]: ("客户端", {b["id"]: "放弃前重试一次。" for b in p["blocks"]})
                                          for p in payload}
    res = translate_tree(ws, tmp_path / ".codoc", language=resolve("zh-Hans"), propose=propose)
    assert res.translated == 1 and ws.store.blocks[c.bid].text("body").live_text() == "Twice?"
    assert ws.store.blocks[d.bid].text("source").live_text().startswith("flowchart")


def test_the_directive_speaks_through_the_kind_codecs(tmp_path):
    """A diagram reaches the agent inside the description, as a mermaid fence.
    An edit to a derived one also reaches it as the dependency request `lower`
    words. An open comment thread reaches it once, as the request its codec words."""
    from codoc.loops.realize import directive
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": "c.py::send"}}], actor="loop")
    add_block(ws, "diagram", {"source": "flowchart TB\n  send --> recv", "derived": True}, after="p1", actor="human:local")
    add_block(ws, "comment", {"thread": "t1", "anchor": "p1", "anchor_text": "once", "body": "Twice?", "scope": "code"},
              after="p1", actor="human:local")
    handoff(ws, "h1", actor="human:local")
    d = directive(ws.store, "h1")
    assert d.count("```mermaid derived\nflowchart TB\n  send --> recv\n```") == 1, d
    assert "Add a dependency or call from `send` to `recv` (the author drew that edge)." in d, d
    assert d.count('Comment on "once": Twice?') == 1, d
    # the comment's words are a request, never the feature's prose changing
    assert 'added: "Twice?"' not in d, d
    ws.close()
