"""How a second process writes: through the daemon's inbox, or as the applier
when no daemon owns the checkout.

The MCP server, the CLI and the hub are separate processes. Only one process
may apply (spec §1), so a writer first asks whether a daemon is alive (the
pidfile). If so it appends submissions to its inbox and the daemon drains them
on the next batch; the caller gets client ids back and may poll the ack file.
If not, it opens the workspace itself, applies, publishes, and closes. Either
way the caller sees one function.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from codoc.calculus.ops import Op
from codoc.substrate import log as L
from codoc.substrate.daemon import daemon_running
from codoc.substrate.log import Submission
from codoc.substrate.publish import publish
from codoc.substrate.workspace import Workspace

CLIENT_IDENTITY = {"ide": "human:local", "cli": "human:local", "mcp": "agent:claude-code", "hub": "human:hub"}


def write(codoc_dir: str | Path, *, client: str, bodies: list[dict], root_dir: str | None = None,
          batch: str | None = None, plan: str | None = None, caused_by: str | None = None,
          wait_s: float = 3.0) -> list[dict]:
    """Submit op bodies as ``client``. Returns one record per body:
    ``{"cid", "op", "conflict"}`` with ``op``/``conflict`` filled when the
    result is known (applied directly, or acked within ``wait_s``)."""
    codoc_dir = Path(codoc_dir)
    actor = CLIENT_IDENTITY.get(client, f"human:{client}")
    if not daemon_running(codoc_dir):
        ws = Workspace(codoc_dir)
        try:
            ops = ws.submit(bodies, actor=actor, batch=batch, plan=plan, caused_by=caused_by)
            publish(codoc_dir, ws.store, root_dir=root_dir or str(codoc_dir.parent))
        finally:
            ws.close()
        return [{"cid": None, "op": o.id, "conflict": o.conflict} for o in ops]
    subs = [Submission(cid=f"{client}-{uuid.uuid4().hex[:12]}", kind=b["kind"], target=b.get("target", {}),
                       payload=b.get("payload", {}), batch=batch or "", plan=b.get("plan", plan),
                       caused_by=b.get("caused_by", caused_by)) for b in bodies]
    L.submit(codoc_dir, client, subs)
    return wait_for_acks(codoc_dir, client, [s.cid for s in subs], wait_s)


def wait_for_acks(codoc_dir: Path, client: str, cids: list[str], wait_s: float) -> list[dict]:
    path = L.inbox_dir(codoc_dir) / f"{client}.ack.jsonl"
    deadline = time.time() + wait_s
    acks: dict[str, dict] = {}
    while time.time() < deadline and len(acks) < len(cids):
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if d.get("cid") in cids:
                        acks[d["cid"]] = d
        if len(acks) < len(cids):
            time.sleep(0.1)
    return [{"cid": c, "op": acks.get(c, {}).get("op"), "conflict": acks.get(c, {}).get("conflict")} for c in cids]


def read(codoc_dir: str | Path) -> Workspace:
    """A read-only workspace: the logs replayed, nothing written."""
    return Workspace(codoc_dir, readonly=True)


def ingest(codoc_dir: str | Path, *, client: str, bid: str, path: str, base_text: str, new_md: str,
           plan: str | None = None, caused_by: str | None = None, root_dir: str | None = None) -> dict:
    """A whole-text write from another process. When a daemon is alive the
    boundary diff still has to run in the applier, so the request is submitted
    as an ``ingest`` submission the daemon expands; without one, run it here."""
    codoc_dir = Path(codoc_dir)
    actor = CLIENT_IDENTITY.get(client, f"human:{client}")
    if not daemon_running(codoc_dir):
        ws = Workspace(codoc_dir)
        try:
            res, ops = ws.ingest(bid, path, base_text=base_text, new_md=new_md, actor=actor, plan=plan,
                                 caused_by=caused_by)
            publish(codoc_dir, ws.store, root_dir=root_dir or str(codoc_dir.parent))
        finally:
            ws.close()
        return {"refused": res.refused, "demoted": res.demote_to_plan, "ops": [o.id for o in ops]}
    sub = Submission(cid=f"{client}-{uuid.uuid4().hex[:12]}", kind="ingest", target={"bid": bid, "path": path},
                     payload={"base_text": base_text, "new_md": new_md}, plan=plan, caused_by=caused_by)
    L.submit(codoc_dir, client, [sub])
    ack = wait_for_acks(codoc_dir, client, [sub.cid], 3.0)[0]
    return {"refused": ack.get("conflict"), "demoted": None, "ops": [ack["op"]] if ack.get("op") else []}
