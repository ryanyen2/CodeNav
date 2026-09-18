"""The one check that says the page and the store agree about the same edits.

The editor is driven for real (jsdom, ProseMirror, every plugin) through random
gestures; every submission it makes is then drained through the real applier,
and the page the editor ended on must be the page the store describes. Either
half alone can be wrong in a way the other hides: the editor's own tests say the
shadow matches the document, and the applier's say an op does what it says, and
between them sat every fault where a gesture recorded something the page never
showed.

The TypeScript half lives in `vscode-codoc/src/test/editor-dom.test.ts` under
`CODOC_ROUNDTRIP`, which this test sets. It needs node and the extension's
`node_modules`; without them it skips.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from codoc.calculus import ops as K
from codoc.substrate import log as L
from codoc.substrate.log import Submission
from codoc.substrate.publish import publish
from codoc.substrate.workspace import Workspace

EXT = Path(__file__).resolve().parents[2] / "vscode-codoc"
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


@pytest.mark.skipif(not (EXT / "node_modules" / "vitest").exists(), reason="the extension's node_modules is not installed")
def test_the_real_editors_ops_replay_to_the_page_it_showed(tmp_path):
    out = tmp_path / "roundtrip"
    out.mkdir()
    cd = tmp_path / "base" / ".codoc"
    ws = Workspace(cd)
    _seed(ws)
    publish(cd, ws.store, root_dir=str(tmp_path / "base"))
    ws.close()
    shutil.copy(cd / "projection.json", out / "projection.json")

    env = {**os.environ, "CODOC_ROUNDTRIP": str(out)}
    run = subprocess.run(["npx", "vitest", "run", "src/test/editor-dom.test.ts", "-t", "round trip"],
                         cwd=EXT, env=env, capture_output=True, text=True, timeout=600)
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
