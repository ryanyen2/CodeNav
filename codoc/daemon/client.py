"""How a second process writes: through the daemon's inbox, or as the applier
when no daemon owns the checkout.

The MCP server, the CLI and the hub are separate processes. Only one process
may apply (spec §1), so a writer first asks whether a daemon is alive (the
pidfile). If so it appends submissions to its inbox and the daemon drains them
on the next batch; the caller gets client ids back and may poll the ack file.
If not, this process is the applier: it opens the workspace once and keeps it
for the life of the process, so a long-lived writer (the MCP server) writes one
session log rather than one per call. A daemon that appears later takes over
at the next call, and the held workspace is closed.

Two daemonless writers are not forbidden — the MCP server holds its workspace
while the person runs `codoc accept` — so ``applying()`` wraps apply-and-publish
in an exclusive lock, and both writes AND reads rebuild when the log grew by a
hand that is not ours since our last turn. Without the lock each held a store
the other had moved on from and the derived files described whichever published
last; without the read half, the agent's `codoc_read_tree` and the `base_text`
its next amend cites describe the tree before the person's `codoc accept`.
The lock is per CALL, never for the life of the process: a lease would leave
the other writer submitting into an inbox with no daemon to drain it.
"""
from __future__ import annotations

import atexit
import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from codoc.calculus.ingest import resolve_pending_refs
from codoc.calculus.ops import TEXT_INSERT
from codoc.daemon.pidfile import daemon_running
from codoc.daemon.publish import publish
from codoc.workspace import Workspace
from codoc.workspace import log as L
from codoc.workspace.layout import APPLIER_LOCK, locked
from codoc.workspace.log import Submission


def _actor(client: str) -> str:
    """The actor a client writes as: one identity function for the inbox path
    and the applier path, so a contributor is the same actor on both."""
    actor = L.actor_for(client)
    if actor is None:
        raise ValueError(f"no identity for client {client!r}")
    return actor


_OWNED: dict[Path, Workspace] = {}
_SEEN: dict[Path, dict[str, int]] = {}      # log file → size, as this process last left it


def _applier(codoc_dir: Path) -> Workspace:
    """This process's workspace for a checkout no daemon owns."""
    ws = _OWNED.get(codoc_dir)
    if ws is None:
        ws = _OWNED[codoc_dir] = Workspace(codoc_dir)
    return ws


def _sizes(codoc_dir: Path) -> dict[str, int]:
    return {p.name: p.stat().st_size for p in L.log_dir(codoc_dir).glob("*.jsonl")}


def _catch_up(codoc_dir: Path, ws: Workspace) -> None:
    """Rebuild when the log grew by a hand that is not ours since our last
    turn. Both the writes and the READS go through this: a held workspace that
    has not looked describes the tree before the person's `codoc accept`, and
    the agent's next amend computes its `base_text` from that."""
    mine = ws.session + ".jsonl"
    was = _SEEN.get(codoc_dir)
    if was is None:
        return
    now = _sizes(codoc_dir)
    if {k: v for k, v in now.items() if k != mine} != {k: v for k, v in was.items() if k != mine}:
        ws.rebuild()


@contextmanager
def applying(codoc_dir: Path):
    """The workspace, under the applier lock, caught up with anybody else's
    writes. Yields inside the lock so apply and publish are one turn."""
    with locked(codoc_dir / APPLIER_LOCK):
        ws = _applier(codoc_dir)
        _catch_up(codoc_dir, ws)
        yield ws
        _SEEN[codoc_dir] = _sizes(codoc_dir)


def release(codoc_dir: Path | None = None) -> None:
    """Close the workspaces this process holds (at exit, or when a daemon
    takes the checkout back)."""
    for key in ([codoc_dir] if codoc_dir is not None else list(_OWNED)):
        ws = _OWNED.pop(Path(key), None) if key is not None else None
        if ws is not None:
            ws.close()


atexit.register(release)


def write(codoc_dir: str | Path, *, client: str, bodies: list[dict], root_dir: str | None = None,
          batch: str | None = None, plan: str | None = None, caused_by: str | None = None,
          wait_s: float = 3.0) -> list[dict]:
    """Submit op bodies as ``client``. Returns one record per body:
    ``{"cid", "op", "conflict"}`` with ``op``/``conflict`` filled when the
    result is known (applied directly, or acked within ``wait_s``)."""
    codoc_dir = Path(codoc_dir)
    actor = _actor(client)
    if not daemon_running(codoc_dir):
        with applying(codoc_dir) as ws:
            ops = ws.submit(bodies, actor=actor, batch=batch, plan=plan, caused_by=caused_by)
            publish(codoc_dir, ws.store, root_dir=root_dir or str(codoc_dir.parent))
        return [{"cid": None, "op": o.id, "conflict": o.conflict} for o in ops]
    release(codoc_dir)
    subs: list[Submission] = []
    insert_cids: list[str] = []
    for b in bodies:
        cid = f"{client}-{uuid.uuid4().hex[:12]}"
        payload = dict(b.get("payload", {}))
        # The daemon resolves ["$cid", cid, k]; a batch-local ["$ins", i, k] becomes that.
        resolve_pending_refs(payload, insert_cids, as_ref=lambda c, k: ["$cid", c, k])
        if b["kind"] == TEXT_INSERT:
            insert_cids.append(cid)
        subs.append(Submission(cid=cid, kind=b["kind"], target=b.get("target", {}), payload=payload,
                               batch=batch or "", plan=b.get("plan", plan), caused_by=b.get("caused_by", caused_by)))
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
    return [{"cid": c, "op": acks.get(c, {}).get("op"), "conflict": acks.get(c, {}).get("conflict"),
             "status": acks.get(c, {}).get("status")} for c in cids]


def read(codoc_dir: str | Path) -> Workspace:
    """A workspace to read from: this process's own when it is the applier
    (so a read sees what it just wrote, caught up with anybody else's writes),
    else the logs replayed read-only."""
    codoc_dir = Path(codoc_dir)
    held = _OWNED.get(codoc_dir)
    if held is None:
        return Workspace(codoc_dir, readonly=True)
    # A read does not take the lock (nothing is written) and does not record
    # what it saw: `_SEEN` is the WRITE turn's memory, and a read must not
    # convince the next write that it has already caught up.
    _catch_up(codoc_dir, held)
    return held


def ingest(codoc_dir: str | Path, *, client: str, bid: str, path: str, base_text: str, new_md: str,
           plan: str | None = None, caused_by: str | None = None, root_dir: str | None = None,
           batch: str | None = None) -> dict:
    """A whole-text write from another process. When a daemon is alive the
    boundary diff still has to run in the applier, so the request is submitted
    as an ``ingest`` submission the daemon expands; without one, run it here."""
    codoc_dir = Path(codoc_dir)
    actor = _actor(client)
    if not daemon_running(codoc_dir):
        with applying(codoc_dir) as ws:
            res, ops = ws.ingest(bid, path, base_text=base_text, new_md=new_md, actor=actor, plan=plan,
                                 caused_by=caused_by, batch=batch)
            publish(codoc_dir, ws.store, root_dir=root_dir or str(codoc_dir.parent))
        return {"refused": res.refused, "ops": [o.id for o in ops],
                "status": None if ops or res.refused else "unchanged"}
    release(codoc_dir)
    sub = Submission(cid=f"{client}-{uuid.uuid4().hex[:12]}", kind="ingest", target={"bid": bid, "path": path},
                     payload={"base_text": base_text, "new_md": new_md}, plan=plan, caused_by=caused_by,
                     batch=batch or "")
    L.submit(codoc_dir, client, [sub])
    ack = wait_for_acks(codoc_dir, client, [sub.cid], 3.0)[0]
    return {"refused": ack.get("conflict"), "ops": [ack["op"]] if ack.get("op") else [],
            "status": ack.get("status")}
