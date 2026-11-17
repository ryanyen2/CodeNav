"""The one check that says the page and the store agree about the same edits.

The editor is driven for real (jsdom, ProseMirror, every plugin) through random
gestures; every submission it makes is then drained through the real applier,
and the page the editor ended on must be the page the store describes. Either
half alone can be wrong in a way the other hides: the editor's own tests say the
shadow matches the document, and the applier's say an op does what it says, and
between them sat every fault where a gesture recorded something the page never
showed.

Two trips. R1 is one actor: every gesture the editor offers, replayed. R2 is
two actors and an agent's plan arriving in the middle of the person's own
edits — the case R1 cannot see, because with one actor the page and the store
never disagree about anything the projection carries. R2 runs the editor twice
over the same seeds (`Math.random` is seeded, so the bids and batch ids match),
once to record and once to replay against the projections the first run's ops
produced.

The TypeScript half lives in `vscode-codoc/src/test/editor-dom.test.ts` under
`CODOC_ROUNDTRIP` and `CODOC_ROUNDTRIP_PHASE`, which these tests set. It needs
node and the extension's `node_modules`; without them they skip.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from codoc.calculus import ops as K
from codoc.workspace import log as L
from codoc.workspace.log import Submission
from codoc.daemon.publish import publish
from codoc.workspace import Workspace

EXT = Path(__file__).resolve().parents[2] / "vscode-codoc"
needs_node = pytest.mark.skipif(not (EXT / "node_modules" / "vitest").exists(),
                                reason="the extension's node_modules is not installed")


def _vitest(out: Path, phase: str, pattern: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "CODOC_ROUNDTRIP": str(out), "CODOC_ROUNDTRIP_PHASE": phase}
    return subprocess.run(["npx", "vitest", "run", "src/test/editor-dom.test.ts", "-t", pattern],
                          cwd=EXT, env=env, capture_output=True, text=True, timeout=900)


def _drain(base: Path, wd: Path, subs: list[dict]) -> tuple[Workspace, list[dict]]:
    """The editor's submissions through the real applier, as the daemon drains
    them; returns the workspace and the acks it wrote, in order."""
    shutil.copytree(base, wd)
    L.submit(wd, "ide", [Submission(cid=s["cid"], kind=s["kind"], target=s["target"], payload=s["payload"],
                                    batch=s.get("batch") or "", plan=s.get("plan")) for s in subs])
    w = Workspace(wd)
    w.drain_inbox()
    acks = [json.loads(line) for line in (L.inbox_dir(wd) / "ide.ack.jsonl").read_text().splitlines()]
    return w, acks[-len(subs):] if subs else []
BASE = [
    ("h1", "heading", 1, "Client"), ("p1", "paragraph", 0, "Retries once before giving up."),
    ("p2", "paragraph", 0, "Second paragraph here."), ("h2", "heading", 1, "Server"),
    ("p3", "paragraph", 0, "Listens on a port."), ("h3", "heading", 2, "Sub"),
    ("p4", "paragraph", 0, "A child feature."), ("h4", "heading", 1, "Tail"),
    ("p5", "paragraph", 0, "Last words."),
]


def _seed(ws: Workspace) -> None:
    after = "START"
    for bid, kind, level, text in BASE:
        ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": bid},
                    "payload": {"kind": kind, "level": level, "after": after}},
                   {"kind": K.TEXT_INSERT, "target": {"bid": bid, "path": "text"},
                    "payload": {"after": "START", "text": text}}], actor="human:local")
        after = bid


def _page(store) -> list[list]:
    return [[b.id, b.kind, b.level if b.kind == "heading" else 0, b.text().live_text()]
            for b in store.ordered() if b.kind != "comment"]


@needs_node
def test_the_real_editors_ops_replay_to_the_page_it_showed(tmp_path):
    out = tmp_path / "roundtrip"
    out.mkdir()
    cd = tmp_path / "base" / ".codoc"
    ws = Workspace(cd)
    _seed(ws)
    publish(cd, ws.store, root_dir=str(tmp_path / "base"))
    ws.close()
    shutil.copy(cd / "projection.json", out / "projection.json")

    run = _vitest(out, "R1", "R1.")
    files = sorted(out.glob("seed*.json"))
    assert run.returncode == 0 and files, f"the editor fuzz did not run:\n{run.stdout[-4000:]}\n{run.stderr[-2000:]}"

    for f in files:
        rec = json.loads(f.read_text())
        wd = tmp_path / f"replay-{rec['seed']}" / ".codoc"
        shutil.copytree(cd, wd)
        L.submit(wd, "ide", [Submission(cid=s["cid"], kind=s["kind"], target=s["target"], payload=s["payload"],
                                        batch=s.get("batch") or "", plan=s.get("plan")) for s in rec["subs"]])
        w = Workspace(wd)
        w.drain_inbox()
        acks = [json.loads(line) for line in (L.inbox_dir(wd) / "ide.ack.jsonl").read_text().splitlines()]
        got = _page(w.store)
        w.close()
        refused = [(a["cid"], a["conflict"]) for a in acks if a.get("conflict")]
        gestures = " → ".join(rec["log"])
        assert refused == [], f"seed {rec['seed']} ({gestures}): the applier refused {refused}"
        assert got == [list(x) for x in rec["expect"]], (
            f"seed {rec['seed']} ({gestures})\n store: {got}\n page:  {rec['expect']}")


@needs_node
def test_two_actors_and_a_plan_arriving_mid_edit_merge_to_the_stores_page(tmp_path):
    """R2. The person edits from P1. Meanwhile Bob (a maintainer) appended to
    p1, put a paragraph after p2 and prefixed p5, and an agent opened a plan
    proposing words in p4: that store's projection is P2, and it PREDATES every
    one of the person's ops. Their ops drain into that store and P3 is
    published. The editor then replays the same gestures, adopts P2 while every
    block it touched is still held, acks with the real acks, and adopts P3 —
    and the page must be the store's page, with no shadow disagreeing at any of
    the three points."""
    out = tmp_path / "r2"
    out.mkdir()
    cd = tmp_path / "base" / ".codoc"
    ws = Workspace(cd)
    _seed(ws)
    publish(cd, ws.store, root_dir=str(tmp_path / "base"))
    ws.close()
    shutil.copy(cd / "projection.json", out / "p1.json")

    # Bob and the agent, on a copy: the store the person's ops will land in.
    cd2 = tmp_path / "base2" / ".codoc"
    shutil.copytree(cd, cd2)
    w2 = Workspace(cd2)
    p1t, p5t = w2.store.blocks["p1"].text(), w2.store.blocks["p5"].text()
    w2.submit([
        {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"},
         "payload": {"after": list(p1t.last_live_ref()), "text": " Bob says so."}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "p6"}, "payload": {"kind": "paragraph", "after": "p2"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "p6", "path": "text"},
         "payload": {"after": "START", "text": "Bob's new paragraph."}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "p5", "path": "text"},
         "payload": {"after": list(p5t.first_ref()), "text": "Bob: "}},
    ], actor="human:local")
    pid = w2.open_plan(actor="agent:claude-code", kind="edit", target="p4")
    p4t = w2.store.blocks["p4"].text()
    w2.submit([{"kind": K.TEXT_INSERT, "target": {"bid": "p4", "path": "text"},
                "payload": {"after": list(p4t.last_live_ref()), "text": " (agent proposes this.)"}}],
              actor="agent:claude-code", plan=pid)
    publish(cd2, w2.store, root_dir=str(tmp_path / "base2"))
    w2.close()
    shutil.copy(cd2 / "projection.json", out / "p2.json")

    run = _vitest(out, "R2A", "R2.")
    recs = sorted(out.glob("r2a-seed*.json"))
    assert run.returncode == 0 and recs, f"phase A did not run:\n{run.stdout[-4000:]}\n{run.stderr[-2000:]}"

    want: dict[int, list] = {}
    refused: list[str] = []
    for f in recs:
        rec = json.loads(f.read_text())
        wd = tmp_path / f"r2-{rec['seed']}" / ".codoc"
        w, acks = _drain(cd2, wd, rec["subs"])
        publish(wd, w.store, root_dir=str(tmp_path))
        want[rec["seed"]] = _page(w.store)
        bad = [(a["cid"], a["conflict"]) for a in acks if a.get("conflict")]
        if bad:
            refused.append(f"seed {rec['seed']} ({' → '.join(rec['log'])}): {bad}")
        p3 = json.loads((wd / "projection.json").read_text())
        w.close()
        (out / f"r2b-seed{rec['seed']:02d}.json").write_text(
            json.dumps({"acks": acks, "p3": p3, "subs": rec["subs"]}))

    run = _vitest(out, "R2B", "R2.")
    outs = sorted(out.glob("r2c-seed*.json"))
    assert run.returncode == 0 and len(outs) == len(recs), f"phase B did not run:\n{run.stdout[-4000:]}\n{run.stderr[-2000:]}"
    problems = list(refused)
    for f in outs:
        rec = json.loads(f.read_text())
        for k in ("faultsP2", "faultsAck", "faultsP3"):
            if rec[k]:
                problems.append(f"seed {rec['seed']} {k}: {rec[k]}")
        if rec["afterP3"] != [list(x) for x in want[rec["seed"]]]:
            problems.append(f"seed {rec['seed']} ({' → '.join(rec['log'])}) after P3"
                            f"\n store: {want[rec['seed']]}\n page:  {rec['afterP3']}")
    assert problems == [], "\n".join(problems)
