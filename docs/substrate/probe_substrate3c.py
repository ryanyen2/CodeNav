"""Review-3 probes, third batch: I6 under plan marks/attaches, inbox poison persistence."""
import json
import random

from codoc.calculus import ops as K
from codoc.calculus.doc import PARAGRAPH
from codoc.calculus.export import export_markdown
from codoc.calculus.ids import START
from codoc.calculus.text import BOLD, LINK
from codoc.substrate import log as L
from codoc.substrate.log import Submission
from codoc.substrate.workspace import Workspace
from tests.loops.test_reflect import seed
from tests.substrate.test_review2 import _random_body


def T(s, bid):
    return s.blocks[bid].text().live_text()


def test_P26_rebuild_equals_live_store_with_plan_marks_and_attaches(tmp_path):
    for seed_n in range(6):
        d = tmp_path / f"w{seed_n}" / ".codoc"
        ws = Workspace(d)
        seed(ws)
        rng = random.Random(seed_n)
        for step in range(220):
            body = _random_body(rng, ws.store, step)
            if body["kind"] == K.TEXT_MARK and body["payload"]["kind"] == LINK and not body["payload"]["attrs"]:
                body["payload"]["attrs"] = {"href": "https://y"}
            got = ws.submit([body], actor=rng.choice(["human:local", "loop"])) [0]
            if got.conflict is None and got.inverse and rng.random() < 0.2:
                ws.submit([dict(b) for b in got.inverse], actor="human:local")
            if step % 25 == 0:
                live = [b for b in ws.store.ordered() if b.kind == PARAGRAPH and b.text().live_len()]
                if not live:
                    continue
                b = rng.choice(live)
                t = b.text()
                n = t.live_len()
                pid = ws.open_plan(actor="agent:x")
                i, j = sorted(rng.sample(range(n), 2)) if n > 1 else (0, 0)
                bodies = [
                    {"kind": K.TEXT_INSERT, "target": {"bid": b.id, "path": "text"}, "payload": {"after": list(t.ref_at(i)), "text": " P"}},
                    {"kind": K.TEXT_MARK, "target": {"bid": b.id, "path": "text"},
                     "payload": {"kind": rng.choice([BOLD, LINK]), "attrs": {} if rng.random() < 0.5 else {"href": "https://p"},
                                 "start": list(t.ref_at(i)), "end": list(t.ref_at(j)), "on": rng.random() < 0.7}},
                    {"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": f"x{step}.py", "symbol": f"x{step}.py::s", "fingerprint": "q"}},
                    {"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": "c.py::send", "fingerprint": f"p{step}"}},
                ]
                for bd in bodies:
                    if bd["kind"] == K.TEXT_MARK and bd["payload"]["kind"] == LINK and not bd["payload"]["attrs"]:
                        bd["payload"]["attrs"] = {"href": "https://p"}
                ids = [o.id for o in ws.submit(bodies, actor="agent:x", plan=pid)]
                r = rng.random()
                verdict = {"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {"subset": rng.sample(ids, rng.randint(1, len(ids)))}} if r < 0.4 \
                    else {"kind": K.PLAN_ACCEPT, "target": {"pid": pid}, "payload": {}} if r < 0.6 \
                    else {"kind": K.PLAN_REJECT, "target": {"pid": pid}, "payload": {}}
                v = ws.submit([verdict], actor="human:local")[0]
                if rng.random() < 0.5 and v.inverse:
                    u = ws.submit([dict(b) for b in v.inverse], actor="human:local")[0]
                    if rng.random() < 0.5 and u.inverse:
                        ws.submit([dict(b) for b in u.inverse], actor="human:local")     # redo
        ws.close()
        ws2 = Workspace(d)
        assert ws2.problems == [], ws2.problems
        assert ws2.store.state_fingerprint() == ws.store.state_fingerprint(), seed_n
        assert export_markdown(ws2.store) == export_markdown(ws.store)
        assert {k: list(v) for k, v in ws2.store.ops_by_block.items()} == {k: list(v) for k, v in ws.store.ops_by_block.items()}, seed_n


def test_P27_a_poison_inbox_line_survives_a_daemon_restart(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": START}}], actor="human:local")
    L.submit(tmp_path / ".codoc", "ide", [Submission(cid="c1", kind="ingest", target={}, payload={"base_text": "", "new_md": "x"})])
    try:
        ws.drain_inbox({"ide": "human:local"})
    except KeyError:
        pass
    ws.close()
    ws2 = Workspace(tmp_path / ".codoc")
    L.submit(tmp_path / ".codoc", "ide", [Submission(cid="c2", kind=K.TEXT_INSERT, target={"bid": "p1", "path": "text"}, payload={"after": START, "text": "ok"})])
    ws2.drain_inbox({"ide": "human:local"})          # after a restart the same line raises again; c2 is never drained
    assert T(ws2.store, "p1") == "ok"
