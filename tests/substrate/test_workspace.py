"""Logs, inbox, rebuild, clock (spec §7)."""
import json

from codoc.calculus import ops as K
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.ids import START
from codoc.workspace import log as L
from codoc.workspace.clock import Clock, fmt
from codoc.workspace.log import Submission
from codoc.workspace import Workspace


def seed(ws: Workspace, actor="human:local"):
    ws.submit([
        {"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": HEADING, "level": 1, "after": START}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "h1", "path": "text"}, "payload": {"after": START, "text": "Client"}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": "h1"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "Retries once."}},
    ], actor=actor)


def test_session_log_is_written_validated_and_rebuilt(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    ws.close()
    files = L.list_sessions(tmp_path / ".codoc")
    assert len(files) == 1
    f = L.read_session(files[0])
    assert f.valid and len(f.ops) == 4 and f.ops[0].id == f"{f.session}:1"
    ws2 = Workspace(tmp_path / ".codoc")
    assert ws2.store.blocks["p1"].text().live_text() == "Retries once."
    assert ws2.store.state_fingerprint() == ws.store.state_fingerprint()
    assert ws2.session != ws.session
    # A second session continues the same tree; both files replay together.
    ws2.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                 "payload": {"after": list(ws2.store.blocks["p1"].text().last_live_ref()), "text": " Then gives up."}}],
               actor="human:local")
    ws2.close()
    ws3 = Workspace(tmp_path / ".codoc")
    assert ws3.store.blocks["p1"].text().live_text() == "Retries once. Then gives up."
    assert len(L.list_sessions(tmp_path / ".codoc")) == 2   # ws3 wrote nothing, so it left no file


def test_tampered_log_is_skipped_whole_and_reported(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    ws.close()
    path = L.list_sessions(tmp_path / ".codoc")[0]
    lines = path.read_text().splitlines()
    d = json.loads(lines[2])
    d["id"] = d["id"].rsplit(":", 1)[0] + ":9"   # out of sequence
    lines[2] = json.dumps(d)
    path.write_text("\n".join(lines) + "\n")
    ws2 = Workspace(tmp_path / ".codoc")
    assert ws2.problems and "out of sequence" in ws2.problems[0].reason
    assert "h1" not in ws2.store.blocks   # nothing from that file applied


def test_inbox_drain_is_idempotent_and_stamps_actor(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    L.submit(tmp_path / ".codoc", "ide", [
        Submission(cid="c1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                   payload={"after": list(ws.store.blocks["p1"].text().last_live_ref()), "text": " More."}),
    ])
    done = ws.drain_inbox({"ide": "human:local"})
    assert len(done) == 1 and done[0].actor == "human:local" and done[0].conflict is None
    assert ws.store.blocks["p1"].text().live_text() == "Retries once. More."
    # Resubmitting the same client id applies nothing.
    L.submit(tmp_path / ".codoc", "ide", [Submission(cid="c1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                                       payload={"after": START, "text": "X"})])
    assert ws.drain_inbox({"ide": "human:local"}) == []
    # An unknown client is ignored.
    L.submit(tmp_path / ".codoc", "stranger", [Submission(cid="z", kind=K.BLOCK_DELETE, target={"bid": "h1"}, payload={})])
    assert ws.drain_inbox({"ide": "human:local"}) == []
    assert ws.store.blocks["h1"].live
    ws.close()


def test_a_new_session_reads_the_inbox_where_the_last_one_stopped(tmp_path):
    """The consumed offset is remembered, so a restart does not re-read every
    submission ever made — which is what lets the ack file be trimmed at all."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    L.submit(cd, "ide", [Submission(cid="c1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                    payload={"after": list(ws.store.blocks["p1"].text().last_live_ref()), "text": " More."})])
    assert len(ws.drain_inbox({"ide": "human:local"})) == 1
    ws.close()
    # The ack that made the drain idempotent is gone; the offset stands in for it.
    (cd / "inbox" / "ide.ack.jsonl").write_text("", encoding="utf-8")
    ws2 = Workspace(cd)
    assert ws2.drain_inbox({"ide": "human:local"}) == []
    assert ws2.store.blocks["p1"].text().live_text() == "Retries once. More."
    L.submit(cd, "ide", [Submission(cid="c2", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                    payload={"after": "START", "text": "New. "})])
    assert len(ws2.drain_inbox({"ide": "human:local"})) == 1
    assert ws2.store.blocks["p1"].text().live_text() == "New. Retries once. More."
    ws2.close()


def test_suggest_role_writes_only_plans(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    denied = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "X"}}],
                       actor="human:contrib")
    assert denied[0].conflict == "authority"
    pid = ws.open_plan(actor="human:contrib", note="suggestion")
    ok = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "X"}}],
                   actor="human:contrib", plan=pid)
    assert ok[0].conflict is None
    assert ws.store.blocks["p1"].text().segs[0].plan == pid


def test_ingest_through_workspace_lands_on_trunk_or_under_a_plan(tmp_path):
    """01 §4 rule 5: a machine may repair a person's prose on the trunk, up to a
    point. Past its gate the same words are a proposal instead, and the gate is
    here because this is where every machine write passes."""
    ws = Workspace(tmp_path / ".codoc")
    seed(ws, actor="loop")
    t = ws.store.blocks["p1"].text()
    res, ops = ws.ingest("p1", "text", base_text=t.live_text(), new_md="Retries three times.", actor="loop")
    assert res.refused is None and all(o.plan is None for o in ops)     # its own prose, a small repair
    assert ws.store.blocks["p1"].text().live_text() == "Retries three times."
    # The same repair to a person's paragraph is a proposal: it deletes a third
    # of it, and a person's prose is held tighter than the loop's own.
    h = Workspace(tmp_path / "h" / ".codoc")
    seed(h)
    res, ops = h.ingest("p1", "text", base_text="Retries once.", new_md="Retries three times.", actor="loop")
    assert ops and {o.plan for o in ops} != {None}
    assert h.store.plans[ops[0].plan].actor == "loop" and h.store.plans[ops[0].plan].status == "open"
    from codoc.calculus.derive import markdown
    assert markdown(h.store.blocks["p1"].text(), "rejected", h.store) == "Retries once."
    h.close()
    pid = ws.open_plan(actor="loop", kind="edit", target="p1")
    t = ws.store.blocks["p1"].text()
    res, ops = ws.ingest("p1", "text", base_text=t.live_text(), new_md="Completely new words here.", actor="loop", plan=pid)
    assert ops and all(o.plan == pid for o in ops)
    assert ws.store.plans[pid].status == "open" and "Retries three times." in ws.store.blocks["p1"].text().live_text()


def test_clock_observes_every_replayed_stamp():
    c = Clock("n")
    a = c.tick()
    b = c.tick()
    assert a < b
    w = c.wall
    c.observe(fmt(w + 1000, 0, "other"))
    assert c.tick() > fmt(w + 999, 0, "zzz")
    # A peer's fast clock moves ours forward too: the log is the truth, and
    # an op typed after replaying it must still sort after it.
    far = fmt(c.wall + 60 * 60 * 1000, 0, "far")
    c.observe(far)
    assert c.tick() > far


def test_rebuild_replays_a_far_future_stamp_and_later_ops_anchor_to_it(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": START}}], actor="human:local")
    ws.clock.wall += 60 * 60 * 1000 - 1                       # a peer's fast clock stamped this
    ins = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "hello"}}], actor="human:local")[0]
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    assert ws2.store.blocks["p1"].text().live_text() == "hello"
    nxt = ws2.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": [ins.id, 4], "text": "!"}}], actor="human:local")[0]
    assert nxt.conflict is None and nxt.at > ins.at


def test_provisional_anchors_chain_a_typing_batch(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws)
    last = list(ws.store.blocks["p1"].text().last_live_ref())
    L.submit(tmp_path / ".codoc", "ide", [
        Submission(cid="t1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": last, "text": " ab"}),
        Submission(cid="t2", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": ["$cid", "t1", 2], "text": "c"}),
        Submission(cid="t3", kind=K.TEXT_DELETE, target={"bid": "p1", "path": "text"}, payload={"from": ["$cid", "t1", 1], "to": ["$cid", "t2", 0]}),
    ])
    done = ws.drain_inbox({"ide": "human:local"})
    assert [o.conflict for o in done] == [None, None, None]
    assert ws.store.blocks["p1"].text().live_text() == "Retries once. "
