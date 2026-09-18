"""Block kinds on the extension interface (`03-extensions.md`)."""
from codoc.calculus import ops as K
from codoc.calculus.derive import attribution, markdown
from codoc.calculus.doc import text_fields
from codoc.calculus.export import export_markdown
from codoc.calculus.ids import START
from codoc.graph.graph import Graph
from codoc.kinds import LowerContext, get, plain
from codoc.kinds.builtins import diagram_lower, edges_of
from codoc.kinds.lift import lift_pass
from codoc.kinds.write import add_block, write_block
from codoc.loops.realize import handoff
from codoc.substrate.workspace import Workspace
from tests.loops.test_reflect import seed


def test_registration_declares_text_fields_and_schema_validation():
    assert text_fields("diagram") == ("source", "caption") and text_fields("image") == ("alt", "caption")
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
    d = add_block(ws, "diagram", {"source": ""}, after="p1", actor="loop")
    assert lift_pass(ws, g) == 1
    src = ws.store.blocks[d.bid].text("source").live_text()
    assert "send --> retry" in src and lift_pass(ws, g) == 0          # idempotent
    # The person draws an edge: the directive names it; a freeform node asks instead.
    old = plain(ws.store.blocks[d.bid], ws.store)
    ctx = LowerContext("h1", [b for b in ws.store.bindings.values() if b.feature == "h1"])
    new = {"source": old["source"] + "\n  retry --> log"}
    assert diagram_lower(old, new, ctx) == "Add a dependency or call from `retry` to `log` (the author drew that edge)."
    assert edges_of(new["source"]) == {("send", "retry"), ("retry", "log")}
    assert "Ask the author" in diagram_lower(old, {"source": old["source"] + "\n  node[Label]"}, ctx)
    assert diagram_lower(old, old, ctx) is None
    # A held feature's diagram is not lifted over the person's edit.
    t = ws.store.blocks["p1"].text()
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": list(t.last_live_ref()), "text": " Mine."}}], actor="human:local")
    handoff(ws, "h1", actor="human:local")
    g2 = Graph([{"src_symbol": "c.py::send", "dst_name": "other", "dst_symbol": "c.py::other", "kind": "call", "src_file": "c.py", "internal": True}])
    assert lift_pass(ws, g2) == 0


def test_mcp_write_block_adds_and_edits_as_the_agent(tmp_path):
    from codoc.mcp import tools
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    ws.close()
    r = tools.write_block(str(cd), kind="diagram", feature_id="h1", content={"source": "flowchart TB\n  a --> b", "caption": "Calls"})
    assert r["ok"] and r["ingested"] and all(i["refused"] is None for i in r["ingested"])
    st = Workspace(cd, readonly=True).store
    blk = st.blocks[r["bid"]]
    assert blk.kind == "diagram" and blk.text("source").live_text() == "flowchart TB\n  a --> b"
    assert {s.actor for s in blk.text("source").segs} == {"agent:claude-code"}
    r2 = tools.write_block(str(cd), bid=r["bid"], content={"caption": "Calls, now"})
    assert r2["ok"] and Workspace(cd, readonly=True).store.blocks[r["bid"]].text("caption").live_text() == "Calls, now"
    assert tools.write_block(str(cd), bid=r["bid"], content={"nope": 1})["error"] == "unknown field 'nope'"
    assert "unknown kind" in tools.write_block(str(cd), kind="gif", feature_id="h1", content={})["error"]


def test_a_feature_carrying_media_survives_every_whole_feature_pass(tmp_path):
    """A block whose Text fields are not called `text` (a diagram, a comment)
    is in the body like any other: the author gate, the blame read, a delete
    plan and a translation all walk its own fields."""
    from codoc.calculus.derive import description, plan_claims
    from codoc.doclang import resolve
    from codoc.loops.context import block_written_by, written_by
    from codoc.loops.reflect import _strike
    from codoc.loops.translate import translate_tree
    from codoc.mcp import tools

    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    d = add_block(ws, "diagram", {"source": "flowchart TB\n  a --> b", "caption": "Calls"}, after="p1", actor="loop")
    c = add_block(ws, "comment", {"thread": "t1", "anchor": "p1", "anchor_text": "once", "body": "Twice?"},
                  after=d.bid, actor="human:local")
    assert block_written_by(ws.store, d.bid) == "loop" and block_written_by(ws.store, c.bid) == "human"
    # The comment is a request about the prose, not the prose: the feature is
    # still machine-written, which is what the amend gate and translate read.
    assert written_by(ws.store, "h1") == "loop"
    assert description(ws.store, "h1") == "Retries once before giving up."       # prose only
    ws.close()
    blame = tools.read_history(str(tmp_path / ".codoc"), "h1")["attribution"]
    assert f"{d.bid}.source" in blame and f"{c.bid}.body" in blame and "p1" in blame
    ws = Workspace(tmp_path / ".codoc")
    struck = _strike(ws.store, [b.id for b in ws.store.feature_body("h1")], "p-1")
    assert sorted(b["target"]["path"] for b in struck) == ["body", "caption", "source", "text", "text"]
    propose = lambda payload, lang, **kw: {p["id"]: ("客户端", "放弃前重试一次。") for p in payload}
    res = translate_tree(ws, tmp_path / ".codoc", language=resolve("zh-Hans"), propose=propose)
    assert res.translated == 1 and ws.store.blocks[c.bid].text("body").live_text() == "Twice?"
    assert ws.store.blocks[d.bid].text("source").live_text().startswith("flowchart")


def test_the_directive_speaks_through_the_kind_codecs(tmp_path):
    """03 §5.1: `consult` is what the realizing agent reads and `lower` runs
    when a hand-off renders. A diagram reaches the prompt as a diagram, not as
    raw mermaid in the person's ink; an open comment thread reaches it once, as
    the request its codec words."""
    from codoc.calculus.derive import directive
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": "c.py::send"}}], actor="loop")
    add_block(ws, "diagram", {"source": "flowchart TB\n  send --> recv"}, after="p1", actor="human:local")
    add_block(ws, "comment", {"thread": "t1", "anchor": "p1", "anchor_text": "once", "body": "Twice?", "scope": "code"},
              after="p1", actor="human:local")
    handoff(ws, "h1", actor="human:local")
    d = directive(ws.store, "h1")
    assert "Diagram of this feature's code (mermaid):\nflowchart TB\n  send --> recv" in d, d
    assert "Add a dependency or call from `send` to `recv` (the author drew that edge)." in d, d
    assert d.count('Comment on "once": Twice?') == 1, d
    # the comment's words are a request, never the feature's prose changing
    assert 'added: "Twice?"' not in d, d
    ws.close()
