"""What a model is told about a feature: who wrote it."""
from codoc.workspace import Workspace
from tests.helpers import H, seed


def test_written_by_treats_a_loop_lifted_diagram_as_machine_prose(tmp_path):
    """context.written_by feeds translate's plan-or-trunk choice and the
    prompt's written_by. A feature whose paragraph the loop wrote and whose
    diagram the loop lifted is machine-written; a person's comment thread on it
    (out of flow, never exported) must not turn the whole feature human."""
    from codoc.kinds.write import add_block
    from codoc.loops.context import block_written_by, written_by
    ws = Workspace(tmp_path / ".codoc"); seed(ws, actor="loop")
    w = add_block(ws, "diagram", {"source": "flowchart TB\n  a --> b"}, after="p1", actor="loop")
    assert w.ok
    assert written_by(ws.store, "h1") == "loop"
    c = add_block(ws, "comment", {"thread": "t1", "anchor": "p1", "anchor_text": "Retries", "body": "why once?"}, after="p1", actor=H)
    assert c.ok, c.refused
    assert block_written_by(ws.store, c.bid) == "human"
    assert written_by(ws.store, "h1") == "loop", "a margin comment made the feature read as human-written"
    ws.close()
