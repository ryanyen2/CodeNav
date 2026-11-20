"""Session logs, the inbox drain, rebuild and merge, the ingest gate, and the clock."""
import json
import random
import shutil
from pathlib import Path

import pytest

from codoc.calculus import ops as K
from codoc.calculus.derive import phase
from codoc.calculus.doc import PARAGRAPH
from codoc.calculus.derive import export_markdown
from codoc.calculus.ids import START
from codoc.calculus.text import BOLD, LINK, SUGGEST_DEL
from codoc.daemon.daemon import Daemon
from codoc.index.changes import ChangeSet
from codoc.loops.realize import directive
from codoc.workspace import Roles, Workspace
from codoc.workspace import log as L
from codoc.workspace import inbox as I
from codoc.workspace.clock import Clock, fmt
from codoc.workspace.inbox import Submission
from tests.helpers import H, T, refs, seed, seed_loop, write_roles, ws_paragraph


def test_session_log_is_written_validated_and_rebuilt(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws, bind=False)
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
    seed(ws, bind=False)
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
    seed(ws, bind=False)
    I.submit(tmp_path / ".codoc", "ide", [
        Submission(cid="c1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                   payload={"after": list(ws.store.blocks["p1"].text().last_live_ref()), "text": " More."}),
    ])
    done = ws.drain_inbox({"ide": "human:local"})
    assert len(done) == 1 and done[0].actor == "human:local" and done[0].conflict is None
    assert ws.store.blocks["p1"].text().live_text() == "Retries once. More."
    # Resubmitting the same client id applies nothing.
    I.submit(tmp_path / ".codoc", "ide", [Submission(cid="c1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                                       payload={"after": START, "text": "X"})])
    assert ws.drain_inbox({"ide": "human:local"}) == []
    # An unknown client is ignored.
    I.submit(tmp_path / ".codoc", "stranger", [Submission(cid="z", kind=K.BLOCK_DELETE, target={"bid": "h1"}, payload={})])
    assert ws.drain_inbox({"ide": "human:local"}) == []
    assert ws.store.blocks["h1"].live
    ws.close()


def test_a_new_session_reads_the_inbox_where_the_last_one_stopped(tmp_path):
    """The consumed offset is remembered, so a restart does not re-read every
    submission ever made — which is what lets the ack file be trimmed at all."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws, bind=False)
    I.submit(cd, "ide", [Submission(cid="c1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                    payload={"after": list(ws.store.blocks["p1"].text().last_live_ref()), "text": " More."})])
    assert len(ws.drain_inbox({"ide": "human:local"})) == 1
    ws.close()
    # The ack that made the drain idempotent is gone; the offset stands in for it.
    (cd / "inbox" / "ide.ack.jsonl").write_text("", encoding="utf-8")
    ws2 = Workspace(cd)
    assert ws2.drain_inbox({"ide": "human:local"}) == []
    assert ws2.store.blocks["p1"].text().live_text() == "Retries once. More."
    I.submit(cd, "ide", [Submission(cid="c2", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                    payload={"after": "START", "text": "New. "})])
    assert len(ws2.drain_inbox({"ide": "human:local"})) == 1
    assert ws2.store.blocks["p1"].text().live_text() == "New. Retries once. More."
    ws2.close()


def test_suggest_role_writes_only_plans(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed(ws, bind=False)
    denied = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "X"}}],
                       actor="human:gh:contrib")
    assert denied[0].conflict == "authority"
    pid = ws.open_plan(actor="human:gh:contrib", note="suggestion")
    ok = ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "X"}}],
                   actor="human:gh:contrib", plan=pid)
    assert ok[0].conflict is None
    assert ws.store.blocks["p1"].text().segs[0].plan == pid


def test_ingest_through_workspace_lands_on_trunk_or_under_a_plan(tmp_path):
    """A machine may repair a person's prose on the trunk up to a point; past
    the gate the same words are a proposal. The gate is in `Workspace.ingest`,
    which every machine write passes through."""
    ws = Workspace(tmp_path / ".codoc")
    seed(ws, actor="loop", bind=False)
    t = ws.store.blocks["p1"].text()
    res, ops = ws.ingest("p1", "text", base_text=t.live_text(), new_md="Retries three times.", actor="loop")
    assert res.refused is None and all(o.plan is None for o in ops)     # its own prose, a small repair
    assert ws.store.blocks["p1"].text().live_text() == "Retries three times."
    # The same repair to a person's paragraph is a proposal: it deletes a third
    # of it, and a person's prose is held tighter than the loop's own.
    h = Workspace(tmp_path / "h" / ".codoc")
    seed(h, bind=False)
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
    seed(ws, bind=False)
    last = list(ws.store.blocks["p1"].text().last_live_ref())
    I.submit(tmp_path / ".codoc", "ide", [
        Submission(cid="t1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": last, "text": " ab"}),
        Submission(cid="t2", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": ["$cid", "t1", 2], "text": "c"}),
        Submission(cid="t3", kind=K.TEXT_DELETE, target={"bid": "p1", "path": "text"}, payload={"from": ["$cid", "t1", 1], "to": ["$cid", "t2", 0]}),
    ])
    done = ws.drain_inbox({"ide": "human:local"})
    assert [o.conflict for o in done] == [None, None, None]
    assert ws.store.blocks["p1"].text().live_text() == "Retries once. "


def test_a_batch_id_another_actor_used_is_not_honoured(tmp_path):
    """A suggester who read the maintainer's batch id from history.json and
    names it gets a fresh batch: an undo of the maintainer's batch sends the
    inverses of the maintainer's ops only. The maintainer goes on in it."""
    from codoc.calculus.window import history_window
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    at_end = {"after": list(refs(ws, "p1")[-1])}
    I.submit(cd, "ide", [Submission(cid="m1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                    payload={**at_end, "text": " Hi"}, batch="b-mine")])
    [mine] = ws.drain_inbox()
    I.submit(cd, "hub-eve", [Submission(cid="e1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                        payload={**at_end, "text": " x"}, batch="b-mine")])
    assert [o.conflict for o in ws.drain_inbox()] == [None]
    I.submit(cd, "ide", [Submission(cid="m2", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                    payload={**at_end, "text": "!"}, batch="b-mine")])
    assert ws.drain_inbox()[0].batch == "b-mine"
    assert {e["actor"] for e in history_window(ws.store)["ops"] if e["batch"] == "b-mine"} == {mine.actor}
    ws.close()


def test_restored_mark_keeps_its_placer_through_rebuild(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ins = ws_paragraph(ws, "p1", "the client")
    ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": LINK, "attrs": {"href": "https://a"}, "start": [ins.id, 0], "end": [ins.id, 9], "on": True}}], actor="human:local")
    disp = ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                       "payload": {"kind": LINK, "attrs": {"href": "https://b"}, "start": [ins.id, 4], "end": [ins.id, 9], "on": True}}], actor="loop")[0]
    ws.submit([dict(b) for b in disp.inverse], actor="human:local")           # undo, as the person
    marks = ws.store.blocks["p1"].text().marks
    assert [(m.attrs["href"], m.actor) for m in marks] == [("https://a", "human:local")]
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    assert ws2.store.state_fingerprint() == ws.store.state_fingerprint()


def _random_body(rng, store, step):
    live = [b for b in store.ordered() if b.kind == PARAGRAPH and b.text().live_len()]
    if not live or rng.random() < 0.1:
        return {"kind": K.BLOCK_ADD, "target": {"bid": f"b{step}"}, "payload": {"kind": PARAGRAPH, "after": START}}
    b = rng.choice(live)
    t = b.text()
    n = t.live_len()
    r = rng.random()
    if r < 0.45:
        return {"kind": K.TEXT_INSERT, "target": {"bid": b.id, "path": "text"},
                "payload": {"after": list(t.ref_at(rng.randrange(n))), "text": rng.choice([" a", " bb", "c "])}}
    if r < 0.65:
        i, j = sorted(rng.sample(range(n), 2)) if n > 1 else (0, 0)
        return {"kind": K.TEXT_DELETE, "target": {"bid": b.id, "path": "text"},
                "payload": {"from": list(t.ref_at(i)), "to": list(t.ref_at(j))}}
    if r < 0.85:
        i, j = sorted(rng.sample(range(n), 2)) if n > 1 else (0, 0)
        return {"kind": K.TEXT_MARK, "target": {"bid": b.id, "path": "text"},
                "payload": {"kind": rng.choice([BOLD, LINK]), "attrs": {} if rng.random() < 0.5 else {"href": "https://x"},
                            "start": list(t.ref_at(i)), "end": list(t.ref_at(j)), "on": rng.random() < 0.7}}
    if r < 0.93 and n > 1:
        return {"kind": K.BLOCK_SPLIT, "target": {"bid": b.id, "path": "text"},
                "payload": {"new": f"s{step}", "at": list(t.ref_at(rng.randrange(n - 1)))}}
    return {"kind": K.BLOCK_MOVE, "target": {"bid": b.id}, "payload": {"after": START}}


def test_workspace_rebuild_equals_live_store_through_verdicts_and_undo(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    rng = random.Random(3)
    ws.clock.wall += 5 * 60 * 1000
    for step in range(250):
        body = _random_body(rng, ws.store, step)
        actor = rng.choice(["human:local", "loop", "agent:x"])
        if body["kind"] == K.TEXT_MARK and body["payload"]["kind"] == LINK and not body["payload"]["attrs"]:
            body["payload"]["attrs"] = {"href": "https://y"}
        got = ws.submit([body], actor=actor)[0]
        if got.conflict is None and got.inverse and rng.random() < 0.2:
            ws.submit([dict(b) for b in got.inverse], actor="human:local")
        if step % 40 == 0:
            live = [b for b in ws.store.ordered() if b.kind == PARAGRAPH and b.text().live_len()]
            if live:
                b = live[0]
                pid = ws.open_plan(actor="agent:x")
                ids = [o.id for o in ws.submit([
                    {"kind": K.TEXT_INSERT, "target": {"bid": b.id, "path": "text"}, "payload": {"after": list(b.text().ref_at(0)), "text": "P"}},
                    {"kind": K.TEXT_INSERT, "target": {"bid": b.id, "path": "text"}, "payload": {"after": list(b.text().ref_at(0)), "text": "Q"}},
                ], actor="agent:x", plan=pid)]
                verdict = {"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {"subset": ids[:1]}} if rng.random() < 0.6 \
                    else {"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}
                v = ws.submit([verdict], actor="human:local")[0]
                if rng.random() < 0.5 and v.inverse:
                    ws.submit([dict(b) for b in v.inverse], actor="human:local")
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    assert ws2.problems == []
    assert ws2.store.state_fingerprint() == ws.store.state_fingerprint()
    assert export_markdown(ws2.store) == export_markdown(ws.store)


def test_torn_and_malformed_inbox_lines(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": START}}], actor="human:local")
    d = I.inbox_dir(tmp_path / ".codoc")
    d.mkdir(parents=True, exist_ok=True)
    line = json.dumps(Submission(cid="c1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": START, "text": "X"}).__dict__)
    (d / "ide.jsonl").write_text(line[:20])
    assert ws.drain_inbox({"ide": "human:local"}) == []
    with (d / "ide.jsonl").open("a") as fh:
        fh.write(line[20:] + "\n")
    assert len(ws.drain_inbox({"ide": "human:local"})) == 1 and T(ws.store, "p1") == "X"
    bad = json.dumps({"cid": "c2", "kind": K.TEXT_INSERT, "target": None, "payload": {}})
    good = json.dumps({"cid": "c3", "kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "Y"}})
    with (d / "ide.jsonl").open("a") as fh:
        fh.write(bad + "\n" + good + "\n")
    assert len(ws.drain_inbox({"ide": "human:local"})) == 1 and T(ws.store, "p1") == "YX"
    acks = [json.loads(l) for l in (d / "ide.ack.jsonl").read_text().splitlines()]
    assert [(a["cid"], a["op"] != "", a["conflict"]) for a in acks] == [("c1", True, None), ("c2", False, "target must be an object"), ("c3", True, None)]
    assert ws.drain_inbox({"ide": "human:local"}) == []          # nothing re-applied


def test_ingest_refuses_a_dead_block_and_a_non_text_path(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": START}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "old"}},
               {"kind": K.BLOCK_DELETE, "target": {"bid": "p1"}, "payload": {}}], actor="human:local")
    res, ops = ws.ingest("p1", "text", base_text="old", new_md="new", actor="loop")
    assert res.refused == "dead_block" and ops == []
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p2"}, "payload": {"kind": PARAGRAPH, "after": START}}], actor="human:local")
    res, ops = ws.ingest("p2", "rank", base_text="", new_md="new", actor="loop")
    assert res.refused == "not_text" and ops == []
    assert all(o.conflict is None for o in ws.store.ops)                   # nothing refused reached the log


def test_deleting_a_bound_heading_detaches_and_undo_reattaches(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    d = ws.submit([{"kind": K.BLOCK_DELETE, "target": {"bid": "h1"}, "payload": {}}], actor="human:local")[0]
    assert "c.py::send" not in ws.store.bindings and d.payload["detached"][0]["symbol"] == "c.py::send"
    ws.submit([dict(b) for b in d.inverse], actor="human:local")
    assert ws.store.bindings["c.py::send"].feature == "h1"
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    assert ws2.store.state_fingerprint() == ws.store.state_fingerprint()


def test_a_malformed_ingest_submission_is_acked_and_the_next_line_applies(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": START}}], actor="human:local")
    I.submit(tmp_path / ".codoc", "ide", [
        Submission(cid="c1", kind="ingest", target={}, payload={"base_text": "", "new_md": "x"}),
        Submission(cid="c2", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": START, "text": "ok"})])
    ws.drain_inbox()
    assert T(ws.store, "p1") == "ok"
    acks = [json.loads(l) for l in (I.inbox_dir(tmp_path / ".codoc") / "ide.ack.jsonl").read_text().splitlines()]
    assert acks[0]["cid"] == "c1" and acks[0]["conflict"].startswith("ingest target.bid")
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    assert ws2.drain_inbox() == []
    # A provisional anchor from a previous drain still resolves.
    I.submit(tmp_path / ".codoc", "ide", [Submission(cid="c3", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": ["$cid", "c2", 1], "text": "!"})])
    got = ws2.drain_inbox()
    assert got[0].conflict is None and T(ws2.store, "p1") == "ok!"


def test_torn_last_line_keeps_the_prefix_and_the_daemon_cuts_it(tmp_path):
    root = tmp_path / "repo"
    cd = root / ".codoc"
    cd.mkdir(parents=True)
    ws = Workspace(cd)
    seed_loop(ws)
    ws.close()
    p = L.list_sessions(cd)[0]
    p.write_bytes(p.read_bytes() + b'{"id": "' + ws.session.encode() + b':6", "at": "0000')
    ws2 = Workspace(cd)
    assert "h1" in ws2.store.blocks and len(ws2.problems) == 1 and ws2.torn
    ws2.close()
    d = Daemon(root, changeset=lambda *a: ChangeSet(), propose=lambda *a, **k: [], printer=lambda *_: None)
    assert L.read_session(p).valid and L.read_session(p).torn_tail is None and "h1" in d.ws.store.blocks
    d.ws.close()


def test_an_undo_that_kills_nothing_is_indexed_the_same_after_rebuild(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ins = ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": START}},
                     {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "abc"}}], actor="human:local")[1]
    ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"}, "payload": {"from": [ins.id, 0], "to": [ins.id, 2]}}], actor="loop")
    ws.submit([dict(b) for b in ins.inverse], actor="human:local")
    live = {k: list(v) for k, v in ws.store.ops_by_block.items()}
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    assert {k: list(v) for k, v in ws2.store.ops_by_block.items()} == live


def test_a_clients_own_scalar_index_survives_normalisation(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": START}}], actor="human:local")
    I.submit(tmp_path / ".codoc", "ide", [
        Submission(cid="c1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": START, "text": "é"}),
        Submission(cid="c2", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": ["$cid", "c1", 1], "text": "x"})])
    ops = ws.drain_inbox()
    assert [o.conflict for o in ops] == [None, None]
    assert T(ws.store, "p1") == "éx"


def test_a_handoff_covers_what_arrived_with_it(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    seed_loop(ws)
    last = ws.store.blocks["p1"].text().last_live_ref()
    I.submit(tmp_path / ".codoc", "ide", [
        Submission(cid="c1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                   payload={"after": list(last), "text": " Twice."}, batch="t1"),
        Submission(cid="c2", kind=K.FEATURE_HANDOFF, target={"fid": "h1"}, payload={"asked": "twice"}, batch="t2")])
    ws.drain_inbox()
    assert phase(ws.store, "h1") == "queued" and "Twice." in directive(ws.store, "h1")


def _fork(tmp_path, name: str, base: Path) -> Path:
    cd = tmp_path / name / ".codoc"
    cd.parent.mkdir()
    shutil.copytree(base, cd)
    return cd


def test_two_clones_appending_after_the_same_block_merge_without_a_rank_collision(tmp_path):
    """Given the same set of log files the rebuild is deterministic and every
    later op still applies. Two clones each append a paragraph after `p1` and
    compute the same rank; after the merge, the next append after either does
    not fail."""
    base = tmp_path / "base" / ".codoc"
    ws = Workspace(base); seed(ws); ws.close()
    a, b = _fork(tmp_path, "a", base), _fork(tmp_path, "b", base)
    wa, wb = Workspace(a), Workspace(b)
    wa.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "pa"}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "pa", "path": "text"}, "payload": {"after": START, "text": "from a"}}], actor=H)
    wb.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "pb"}, "payload": {"kind": PARAGRAPH, "after": "p1"}},
               {"kind": K.TEXT_INSERT, "target": {"bid": "pb", "path": "text"}, "payload": {"after": START, "text": "from b"}}], actor=H)
    wa.close(); wb.close()
    # git merge: the union of session files
    for f in L.list_sessions(b):
        if not (a / "log" / f.name).exists():
            shutil.copy(f, a / "log" / f.name)
    m = Workspace(a)
    assert {x.id for x in m.store.ordered()} == {"h1", "p1", "pa", "pb"}
    ranks = [m.store.blocks[x].rank for x in ("pa", "pb")]
    assert len(set(ranks)) == 2, f"two live blocks share one rank: {ranks}"
    first = m.store.ordered()[2].id
    op = m.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "pc"}, "payload": {"kind": PARAGRAPH, "after": first}}], actor=H)[0]
    assert op.conflict is None, op.conflict
    m.close()


def test_a_read_only_command_leaves_no_session_file_behind(tmp_path):
    """`codoc rebuild` and `codoc snapshot` open a writing Workspace and write
    nothing. A session file is created by its first op, so such a run leaves
    no log file behind."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()
    n = len(L.list_sessions(cd))
    for _ in range(3):
        w = Workspace(cd); w.close()
    assert len(L.list_sessions(cd)) == n, "a Workspace that wrote no op left a log file"


def test_an_amend_ack_reports_a_refused_rewrite_even_when_another_landed(tmp_path):
    """One rewrite lands on the trunk and the other is refused because an open
    plan's words are in its block. The ack names the refusal."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed_loop(ws)
    ws_paragraph(ws, "p2", "Logs each retry.", after="p1", actor="loop")
    pid = ws.open_plan(actor="agent:claude-code", kind="edit", target="p1")
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": list(ws.store.blocks["p1"].text().ref_at(5)), "text": " X"}}],
              actor="agent:claude-code", plan=pid)
    I.submit(cd, "agent-claude-code", [Submission(cid="a1", kind="amend", target={"fid": "h1"}, payload={
        "fid": "h1", "rewrites": [{"bid": "p1", "new_md": "Retries twice."},
                                  {"bid": "p2", "new_md": "Logs each retry once.", "base_text": "Logs each retry."}]})])
    ws.drain_inbox()
    ack = json.loads((I.inbox_dir(cd) / "agent-claude-code.ack.jsonl").read_text().splitlines()[-1])
    assert ack["status"] == "trunk" and ack["conflict"] == "p1: open_plan", ack
    ws.close()


def test_a_suggesters_strike_covers_what_it_names_not_the_chars_it_lists(tmp_path):
    """A one-character strike that lists every character of the paragraph in
    `attrs.chars` strikes that one character when accepted."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    every = ",".join(f"{op}:{k}" for op, k in r)
    I.submit(cd, "hub-mallory", [Submission(cid="s1", kind=K.TEXT_MARK, target={"bid": "p1", "path": "text"}, payload={
        "kind": SUGGEST_DEL, "attrs": {"chars": every}, "start": list(r[-1]), "end": list(r[-1]), "on": True})])
    pid = ws.drain_inbox()[-1].plan
    assert ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor=H)[0].conflict is None
    assert T(ws.store, "p1") == "Retries once"
    ws.close()


def test_a_mark_naming_an_op_that_placed_no_mark_is_the_senders(tmp_path):
    """A restore keeps the placer it names only when that op is a mark the
    named actor placed at that clock. Naming the owner's insert does not let
    the agent's bold read as the owner's."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    owners = ws.store.by_id[r[0][0]]
    I.submit(cd, "agent-claude-code", [Submission(cid="m1", kind=K.TEXT_MARK, target={"bid": "p1", "path": "text"}, payload={
        "kind": BOLD, "start": list(r[0]), "end": list(r[6]), "on": True,
        "actor": H, "op": owners.id, "at": owners.at})])
    ws.drain_inbox()
    assert [(m.kind, m.actor) for m in ws.store.blocks["p1"].text().marks] == [(BOLD, "agent:claude-code")]
    ws.close()


def test_undoing_the_removal_of_a_widened_mark_gives_it_back_to_its_placer(tmp_path):
    """Widening a mark keeps its placer and takes the widener's clock. The undo
    of its removal is the inverse the applier recorded, so it is honoured."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")

    def bold(on: bool, end: int) -> dict:
        return {"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                "payload": {"kind": BOLD, "start": list(r[0]), "end": list(r[end]), "on": on}}

    ws.submit([bold(True, 6)], actor="loop")
    ws.submit([bold(True, 11)], actor=H)
    removal = ws.submit([bold(False, 11)], actor=H)[0]
    assert not ws.store.blocks["p1"].text().marks
    undo = removal.inverse[0]
    ws.apply_submissions([("ide", H, Submission(cid="u1", kind=undo["kind"], target=undo["target"],
                                               payload=undo["payload"]))])
    assert [(m.kind, m.actor) for m in ws.store.blocks["p1"].text().marks] == [(BOLD, "loop")]
    # The loop removes it; the same undo sent again is the person's bold, not the loop's.
    ws.submit([bold(False, 11)], actor="loop")
    ws.apply_submissions([("ide", H, Submission(cid="u2", kind=undo["kind"], target=undo["target"],
                                               payload=undo["payload"]))])
    assert [(m.kind, m.actor) for m in ws.store.blocks["p1"].text().marks] == [(BOLD, H)]
    ws.close()


def test_undoing_a_restore_kills_as_the_delete_so_its_undo_still_gives_the_words_back(tmp_path):
    """A person deletes words, undoes the delete, and undoes that undo: the
    words die again as the delete killed them, so undoing the delete once more
    gives them back. Sent by anyone else, the same delete kills as its sender."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws)
    r = refs(ws, "p1")
    dele = ws.submit([{"kind": K.TEXT_DELETE, "target": {"bid": "p1", "path": "text"},
                       "payload": {"from": list(r[0]), "to": list(r[7])}}], actor=H)[0]

    def undo(cid, op, actor=H):
        (b,) = op.inverse
        return ws.apply_submissions([("ide", actor, Submission(cid=cid, kind=b["kind"], target=b["target"],
                                                               payload=b["payload"]))])[0]
    undo("u1", dele)
    restore = ws.store.ops[-1]
    assert T(ws.store, "p1") == "Retries once." and restore.kind == K.TEXT_RESTORE
    undo("u2", restore)
    assert T(ws.store, "p1") == "once." and {g.dead for g in ws.store.blocks["p1"].text().segs if not g.live} == {dele.id}
    undo("u3", dele)
    assert T(ws.store, "p1") == "Retries once."
    undo("u4", restore, actor="human:b")
    forged = ws.store.ops[-1]
    assert forged.kind == K.TEXT_DELETE and "by" not in forged.payload
    assert {g.dead for g in ws.store.blocks["p1"].text().segs if not g.live} == {forged.id}
    ws.close()


def test_a_proposal_is_decided_whole_by_the_applier(tmp_path):
    """The agent may never see the ack (a busy daemon). Whatever it sees, a
    proposal that lands nothing leaves no plan; one that lands is one plan."""
    ws = Workspace(tmp_path / ".codoc"); seed(ws)

    def propose(pid: str, text: str):
        return ws.apply_submissions([("agent-claude-code", "agent:claude-code", Submission(
            cid=pid, kind="propose", target={"pid": pid}, payload={
                "kind": "edit", "target": "h1", "note": "", "build": True,
                "rewrites": [{"bid": "p1", "new_md": text}]}))])[0]

    same = propose("p-same", T(ws.store, "p1"))
    assert same.status == "unchanged" and "p-same" not in ws.store.open_plans
    changed = propose("p-new", "Retries twice.")
    assert changed.status == "plan:p-new" and ws.store.open_plans["p-new"].ops
    # A malformed part fails before anything lands, so no half-written plan stays.
    bad = ws.apply_submissions([("agent-claude-code", "agent:claude-code", Submission(
        cid="p-bad", kind="propose", target={"pid": "p-bad"}, payload={
            "kind": "edit", "target": "h1", "note": "", "rewrites": [], "strikes": ["nope"],
            "bodies": [{"kind": K.BLOCK_ADD, "target": {"bid": "nb"}, "payload": {"kind": "paragraph", "after": "p1"}}]}))])[0]
    assert bad.conflict and "p-bad" not in ws.store.plans and "nb" not in ws.store.blocks
    ws.close()


def test_an_old_undo_after_a_verdict_removed_the_mark_is_the_senders_bold(tmp_path):
    """The loop's bold, removed and restored by H, is then removed by a plan
    another person accepts. H sending the old undo again bolds the words as H."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()
    write_roles(cd, {"human:other": "owner"})
    ws = Workspace(cd)
    r = refs(ws, "p1")

    def bold(on: bool) -> list:
        return [{"kind": K.TEXT_MARK, "target": {"bid": "p1", "path": "text"},
                 "payload": {"kind": BOLD, "start": list(r[0]), "end": list(r[6]), "on": on}}]

    ws.submit(bold(True), actor="loop")
    removal = ws.submit(bold(False), actor=H)[0]
    undo = removal.inverse[0]

    def send_undo(cid: str) -> None:
        ws.apply_submissions([("ide", H, Submission(cid=cid, kind=undo["kind"], target=undo["target"],
                                                   payload=undo["payload"]))])

    send_undo("u1")
    pid = ws.open_plan(actor="agent:claude-code", kind="edit", target="h1")
    ws.submit(bold(False), actor="agent:claude-code", plan=pid)
    assert ws.submit([{"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}}], actor="human:other")[0].conflict is None
    assert not [m for m in ws.store.blocks["p1"].text().marks if m.kind == BOLD]
    send_undo("u2")
    assert [(m.kind, m.actor) for m in ws.store.blocks["p1"].text().marks if m.kind == BOLD] == [(BOLD, H)]
    ws.close()


def test_only_a_coding_agents_plan_asks_for_a_build(tmp_path):
    cd = tmp_path / ".codoc"
    ws = Workspace(cd); seed(ws); ws.close()
    write_roles(cd, {"human:gh:bob": "suggest"})
    ws = Workspace(cd)
    opened = {"kind": "edit", "target": "h1", "build": True}
    ws.apply_submissions([("hub-bob", "human:gh:bob", Submission(cid="b", kind=K.PLAN_OPEN, target={"pid": "pb"},
                                                                payload=dict(opened)))])
    ws.apply_submissions([("agent-claude-code", "agent:claude-code", Submission(cid="a", kind=K.PLAN_OPEN, target={"pid": "pa"},
                                                                 payload=dict(opened)))])
    assert not ws.store.plans["pb"].build and ws.store.plans["pa"].build
    ws.close()


def test_a_machine_ingest_on_the_trunk_never_deletes_an_open_plans_words(tmp_path):
    """A writer that read the live text and was shown it without the plan's
    words would delete them on the trunk, so the write is refused."""
    ws = Workspace(tmp_path / ".codoc"); seed_loop(ws)
    pid = ws.open_plan(actor="agent:claude-code", kind="edit", target="p1")
    anchor = list(ws.store.blocks["p1"].text().ref_at(len("Retries once") - 1))
    ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
                "payload": {"after": anchor, "text": " with backoff"}}], actor="agent:claude-code", plan=pid)
    res, ops = ws.ingest("p1", "text", base_text=T(ws.store, "p1"), new_md="Retries once before giving up!",
                         actor="loop")
    assert res.refused == "open_plan" and ops == []
    assert T(ws.store, "p1") == "Retries once with backoff before giving up."
    ws.close()


@pytest.mark.parametrize("fold_first", [False, True], ids=["restart", "fold, then restart"])
def test_a_submission_logged_before_its_ack_is_acked_from_the_log_and_not_applied_again(tmp_path, monkeypatch,
                                                                                        fold_first):
    """The applier stops (SIGTERM from a closing window, or SIGKILL) after it
    logged a submission's op and before it wrote the ack or the offset. The
    next workspace to open, a daemon's or the one `codoc snapshot` folds with,
    finds the submission named in the log and acks it from there, so the words
    are not typed twice even after the fold archives that log."""
    from tests.helpers import fold
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed_loop(ws)
    I.submit(cd, "ide", [Submission(cid="k1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                    payload={"after": list(ws.store.blocks["p1"].text().last_live_ref()),
                                             "text": " Typed."})])

    def stop(*_a, **_k):
        raise SystemExit(0)
    monkeypatch.setattr(I, "ack", stop)
    with pytest.raises(SystemExit):
        ws.drain_inbox(identity={"ide": H})
    monkeypatch.undo()
    typed = ws.store.ops[-1].id
    if fold_first:
        fold(cd)

    again = Workspace(cd)
    again.drain_inbox(identity={"ide": H})
    assert T(again.store, "p1") == "Retries once before giving up. Typed."
    acks = [json.loads(line) for line in (I.inbox_dir(cd) / "ide.ack.jsonl").read_text().splitlines()]
    assert acks == [{"cid": "k1", "op": typed, "conflict": None}]
    again.close()


def test_the_drain_applies_submissions_in_the_order_they_were_appended(tmp_path):
    """The person replaces "once." with "twice."; a moment later the agent's
    amend rewords the same sentence. The person's edit goes first, whatever
    the inbox names, so the machine gate sees a feature with unsent edits and
    the agent's words do not land on top of the person's."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed(ws)
    t = ws.store.blocks["p1"].text()
    once = t.ref_at(8)
    I.submit(cd, "ide", [
        Submission(cid="i1", kind=K.TEXT_DELETE, target={"bid": "p1", "path": "text"},
                   payload={"segments": [[once[0], once[1], 5]]}, batch="b1"),
        Submission(cid="i2", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                   payload={"after": list(t.ref_at(7)), "text": "twice."}, batch="b1")])
    I.submit(cd, "agent-claude-code", [Submission(
        cid="a1", kind="amend", target={"fid": "h1"},
        payload={"rewrites": [{"bid": "p1", "path": "text", "new_md": "Retries once on timeout.",
                               "base_text": "Retries once."}]})])
    ws.drain_inbox(identity={"ide": H, "agent-claude-code": "agent:claude-code"})
    assert T(ws.store, "p1") == "Retries twice."
    ws.close()


def test_the_drain_applies_the_agents_amend_first_when_it_was_appended_first(tmp_path):
    """The other direction: the order is the append time, not which kind of
    client wrote the line."""
    import time
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed_loop(ws)
    I.submit(cd, "agent-claude-code", [Submission(cid="a1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                                  payload={"after": "START", "text": "A"})])
    time.sleep(0.01)
    I.submit(cd, "ide", [Submission(cid="i1", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"},
                                    payload={"after": "START", "text": "B"})])
    ops = ws.drain_inbox(identity={"ide": H, "agent-claude-code": "agent:claude-code"})
    assert [o.actor for o in ops] == ["agent:claude-code", H]
    ws.close()


def test_a_whole_line_that_is_not_an_op_is_reported_and_never_cut(tmp_path):
    """A torn tail is a last line that does not parse. A line that parses was
    written whole: the file is reported and skipped, and nothing rewrites it."""
    from codoc.workspace.log import read_session, truncate_torn
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed_loop(ws)
    ws.close()
    [path] = L.list_sessions(cd)
    path.write_text(path.read_text() + '{"closed": 1}\n')
    before = path.read_bytes()
    f = read_session(path)
    assert not f.valid and f.torn_tail is None and "not an op" in f.problems[0].reason
    assert not truncate_torn(f) and path.read_bytes() == before


def test_a_client_cannot_say_another_actor_set_a_bindings_hashes(tmp_path):
    """`set_by` tells the reflect pass whether the loop already reviewed the
    code under a binding. An agent's refresh is set by the agent, whatever its
    payload claims."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed_loop(ws)
    I.submit(cd, "agent-claude-code", [Submission(cid="r1", kind=K.BIND_REFRESH, target={"fid": "h1"}, payload={
        "symbol": "c.py::send", "fingerprint": "f9", "types_hash": "t1", "set_by": "loop"})])
    ws.drain_inbox(identity={"agent-claude-code": "agent:claude-code"})
    assert ws.store.bindings["c.py::send"].set_by == "agent:claude-code"
    ws.close()


def test_undoing_a_kind_change_takes_the_bindings_back_as_the_block_kept_them(tmp_path):
    """The undo of a heading made a paragraph names the symbols to take back;
    their hashes and setter come from the block, so a client cannot say the
    loop set them, and a fold keeps the same setter."""
    from codoc.calculus.doc import HEADING
    from codoc.workspace.snapshot import write_snapshot
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    seed_loop(ws)
    ws.submit([{"kind": K.BIND_REFRESH, "target": {"fid": "h1"},
                "payload": {"symbol": "c.py::send", "fingerprint": "f2", "types_hash": "t1"}}], actor="agent:claude-code")
    ident = {"editor": H}
    I.submit(cd, "editor", [Submission(cid="k1", kind=K.BLOCK_SET, target={"bid": "h1", "path": "kind"},
                                       payload={"value": PARAGRAPH})])
    ws.drain_inbox(identity=ident)
    assert "c.py::send" not in ws.store.bindings
    I.submit(cd, "editor", [Submission(cid="k2", kind=K.BLOCK_SET, target={"bid": "h1", "path": "kind"}, payload={
        "value": HEADING, "level": 1, "bindings": [{"fid": "h1", "symbol": "c.py::send", "fingerprint": "f9",
                                                    "types_hash": "t9", "set_by": "loop"}]})])
    ws.drain_inbox(identity=ident)
    back = ws.store.bindings["c.py::send"]
    assert (back.fingerprint, back.types_hash, back.set_by) == ("f2", "t1", "agent:claude-code")
    fp = ws.store.state_fingerprint()
    assert write_snapshot(cd, ws.store, ws.clock)
    ws.close()
    assert Workspace(cd, readonly=True).store.state_fingerprint() == fp


def test_a_contributor_cannot_dress_an_attach_as_a_loop_refresh(tmp_path):
    """A hub client's attach claims the loop set the hashes and carries a
    refresh's `prev_*`. A contributor's is refused outright: bindings are not
    theirs to write. A maintainer's is recorded as set by the maintainer, with
    none of the refresh fields it named."""
    cd = tmp_path / ".codoc"
    ws = Workspace(cd)
    write_roles(cd, {"human:gh:bob": "maintainer"})
    ws.roles = Roles.load(cd); ws.store.policy = ws.roles.policy
    seed_loop(ws)
    dressed = {"symbol": "c.py::retry", "fingerprint": "f9", "types_hash": "t9", "set_by": "loop",
               "prev_fingerprint": "f9", "prev_types_hash": "t9", "prev_set_by": "loop"}
    I.submit(cd, "hub-alice", [Submission(cid="a1", kind=K.BIND_ATTACH, target={"fid": "h1"}, payload=dressed)])
    assert [o.conflict for o in ws.drain_inbox() if o.kind == K.BIND_ATTACH] == ["authority"]
    assert "c.py::retry" not in ws.store.bindings
    I.submit(cd, "hub-bob", [Submission(cid="b1", kind=K.BIND_ATTACH, target={"fid": "h1"}, payload=dressed)])
    (attach,) = [o for o in ws.drain_inbox() if o.kind == K.BIND_ATTACH]
    assert attach.conflict is None
    assert ws.store.bindings["c.py::retry"].set_by == "human:gh:bob"
    assert not {"set_by", "prev_fingerprint", "prev_set_by"} & set(attach.payload)
    ws.close()

