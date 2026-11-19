"""What a model is told about a feature: who wrote it."""
from codoc.workspace import Workspace
from tests.helpers import kind_block, seed


def test_written_by_treats_a_loop_lifted_diagram_as_machine_prose(tmp_path):
    """context.written_by feeds the prompt's written_by. A feature whose paragraph the loop wrote and whose
    diagram the loop lifted is machine-written; a person's comment thread on it
    (out of flow, never exported) must not turn the whole feature human."""
    from codoc.loops.context import block_written_by, written_by
    ws = Workspace(tmp_path / ".codoc"); seed(ws, actor="loop")
    kind_block(ws, "diagram", {"source": "flowchart TB\n  a --> b"}, after="p1", actor="loop")
    assert written_by(ws.store, "h1") == "loop"
    c = kind_block(ws, "comment", {"thread": "t1", "anchor": "p1", "anchor_text": "Retries", "body": "why once?"}, after="p1")
    assert block_written_by(ws.store, c) == "human"
    assert written_by(ws.store, "h1") == "loop", "a margin comment made the feature read as human-written"
    ws.close()


def test_a_kind_without_code_reaches_the_model_by_its_words(tmp_path, monkeypatch):
    """A block whose kind this process has no code for still shows its Text
    fields' words, under its kind's name, in the context and the directive."""
    from codoc.calculus import doc
    from codoc.loops.context import feature_entry
    from codoc.loops.realize import directive
    from tests.helpers import handoff
    ws = Workspace(tmp_path / ".codoc"); seed(ws)
    monkeypatch.setitem(doc._REGISTERED, "widget", doc.Layout(("caption",)))
    kind_block(ws, "widget", {"url": "u", "caption": "A **plot**"}, after="p1")
    monkeypatch.delitem(doc._REGISTERED, "widget")
    handoff(ws, "h1", actor="human:local")
    assert feature_entry(ws.store, "h1")["kind_blocks"] == ["widget: A **plot**"]
    assert "widget: A **plot**" in directive(ws.store, "h1")
    ws.close()
