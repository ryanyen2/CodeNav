"""How a second process writes: through the daemon's inbox, or as the applier
when no daemon owns the checkout.

The MCP server, the CLI and the hub are separate processes. Only one process
may apply (docs/concurrency.md, Processes), so a writer takes the applier lock
and asks whether a daemon is alive (the pidfile). If so it appends submissions
to its inbox and the daemon drains them on the next batch; the caller gets
client ids back and may poll the ack file. If not, this process is the
applier: it opens the workspace once and keeps it for the life of the process,
so a long-lived writer (the MCP server) writes one session log rather than one
per call. A daemon that appears later takes over at the next call, and the
held workspace is closed. Both ways a submission is applied by
``Workspace.apply_submission``.

Two daemonless writers are allowed (the MCP server holds its workspace while
the person runs `codoc accept`), so ``applying()`` wraps apply-and-publish
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

from codoc.calculus.ids import new_batch_id
from codoc.calculus.ingest import resolve_pending_refs
from codoc.calculus.ops import TEXT_INSERT
from codoc.daemon.epoch import epoch_alive
from codoc.daemon.pidfile import daemon_running
from codoc.daemon.publish import publish
from codoc.index.git import head_sha
from codoc.loops.realize import settle
from codoc.workspace import Workspace
from codoc.workspace import log as L
from codoc.workspace.layout import APPLIER_LOCK, locked
from codoc.workspace.log import Submission


def _actor(client: str, codoc_dir: Path) -> str:
    """The actor a client writes as: one identity function for the inbox path
    and the applier path, so a contributor is the same actor on both."""
    actor = L.actor_for(client, codoc_dir)
    if actor is None:
        raise ValueError(f"no identity for client {client!r}")
    return actor


_OWNED: dict[Path, Workspace] = {}
_SEEN: dict[Path, dict[str, int]] = {}      # log file → size, as this process last left it
_HEAD: dict[Path, str] = {}                 # the HEAD sha this process last wrote under


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


def _follow_head(codoc_dir: Path, ws: Workspace) -> bool:
    """When ``HEAD`` moved since this process's last turn (a commit, a checkout,
    a pull), rebuild from the logs now on disk and start a new session file,
    as the daemon does. A session file is never appended to after the commit
    that may have recorded it. Returns whether it rebuilt."""
    head = head_sha(str(codoc_dir.parent))
    was = _HEAD.get(codoc_dir)
    _HEAD[codoc_dir] = head
    if was is None or was == head:
        return False
    ws.rebuild()
    ws.rotate_session()
    return True


class DaemonRunning(RuntimeError):
    """A daemon owns the checkout: submit to its inbox."""


@contextmanager
def applying(codoc_dir: Path):
    """The workspace, under the applier lock, caught up with anybody else's
    writes and with ``HEAD``. Yields inside the lock so apply and publish are
    one turn. Raises ``DaemonRunning`` when a daemon owns the checkout, asked
    under the lock: a daemon that started while this process waited for the
    lock rebuilt without what this process would apply now, and never reads it."""
    with locked(codoc_dir / APPLIER_LOCK):
        if daemon_running(codoc_dir):
            release(codoc_dir)
            raise DaemonRunning(str(codoc_dir))
        ws = _applier(codoc_dir)
        if not _follow_head(codoc_dir, ws):
            _catch_up(codoc_dir, ws)
        yield ws
        _SEEN[codoc_dir] = _sizes(codoc_dir)


def release(codoc_dir: Path | None = None) -> None:
    """Close the workspaces this process holds (at exit, or when a daemon
    takes the checkout back)."""
    for key in ([codoc_dir] if codoc_dir is not None else list(_OWNED)):
        ws = _OWNED.pop(Path(key), None) if key is not None else None
        _HEAD.pop(Path(key), None)
        if ws is not None:
            ws.close()


atexit.register(release)


def write(codoc_dir: str | Path, *, client: str, bodies: list[dict], root_dir: str | None = None,
          batch: str | None = None, plan: str | None = None, caused_by: str | None = None,
          wait_s: float = 3.0) -> list[dict]:
    """Submit op bodies as ``client``, as one batch. Returns one record per
    body: ``{"cid", "op", "conflict", "status"}`` with ``op``/``conflict``
    filled when the result is known (applied here, or acked within ``wait_s``)."""
    batch = batch or new_batch_id()
    subs: list[Submission] = []
    insert_cids: list[str] = []
    for b in bodies:
        cid = _cid(client)
        payload = dict(b.get("payload", {}))
        # The applier resolves ["$cid", cid, k]; a batch-local ["$ins", i, k] becomes that.
        resolve_pending_refs(payload, insert_cids, as_ref=lambda c, k: ["$cid", c, k])
        if b["kind"] == TEXT_INSERT:
            insert_cids.append(cid)
        subs.append(Submission(cid=cid, kind=b["kind"], target=b.get("target", {}), payload=payload,
                               batch=batch, plan=b.get("plan", plan), caused_by=b.get("caused_by", caused_by)))
    return _send(Path(codoc_dir), client, subs, root_dir, wait_s)


def _cid(client: str) -> str:
    return f"{client}-{uuid.uuid4().hex[:12]}"


def _send(codoc_dir: Path, client: str, subs: list[Submission], root_dir: str | None, wait_s: float) -> list[dict]:
    """Apply ``subs`` as ``client``: here when no daemon owns the checkout
    (``Workspace.apply_submissions``, what the daemon's drain runs), else
    through its inbox, waiting up to ``wait_s`` for the acks. One ack-shaped
    record per submission, ``{"cid", "op", "conflict", "status", "ops"}``;
    through the inbox ``ops`` holds the acked first op only."""
    actor = _actor(client, codoc_dir)
    try:
        with applying(codoc_dir) as ws:
            got = ws.apply_submissions(client, actor, subs)
            if not epoch_alive(codoc_dir):               # as the daemon does (Daemon._settle)
                settle(ws, sha=_HEAD.get(codoc_dir, ""))
            publish(codoc_dir, ws.store, root_dir=root_dir or str(codoc_dir.parent))
    except DaemonRunning:
        L.submit(codoc_dir, client, subs)
        return [{**a, "ops": [a["op"]] if a["op"] else []}
                for a in wait_for_acks(codoc_dir, client, [s.cid for s in subs], wait_s)]
    return [{"cid": s.cid, "op": a.ops[0].id if a.ops else None, "conflict": a.conflict, "status": a.status,
             "ops": [o.id for o in a.ops]} for s, a in zip(subs, got)]


def settle_closed_epoch(codoc_dir: str | Path) -> None:
    """Settle what an agent epoch held back, once, when no daemon owns the
    checkout. Every MCP write of a Claude Code turn lands while its epoch is
    open, so ``_send`` settles nothing, and with no daemon nothing applies after
    the Stop hook closes it. The hook starts this in a detached process
    (``python -m codoc.daemon.client <codoc_dir>``). An epoch the next turn has
    already reopened is left to that turn's own close."""
    codoc_dir = Path(codoc_dir)
    try:
        with applying(codoc_dir) as ws:
            if not epoch_alive(codoc_dir) and settle(ws, sha=_HEAD.get(codoc_dir, "")):
                publish(codoc_dir, ws.store, root_dir=str(codoc_dir.parent))
    except DaemonRunning:
        pass                     # the daemon's batch that sees the epoch close settles it


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
    (so a read sees its own writes, caught up with anybody else's),
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
    as an ``ingest`` submission the applier expands."""
    sub = Submission(cid=_cid(client), kind="ingest", target={"bid": bid, "path": path},
                     payload={"base_text": base_text, "new_md": new_md}, plan=plan, caused_by=caused_by,
                     batch=batch or "")
    a = _send(Path(codoc_dir), client, [sub], root_dir, 3.0)[0]
    return {"refused": a["conflict"], "ops": a["ops"], "status": a["status"]}


def _decide(codoc_dir: str | Path, client: str, kind: str, target: dict, payload: dict, caused_by: str | None,
            root_dir: str | None, wait_s: float) -> dict:
    """Send one submission the applier decides whole (``amend``, ``propose``).
    Returns ``{"status", "refused", "ops"}``; ``status`` is ``plan:<id>``,
    ``trunk``, ``unchanged``, or None when no ack arrived within ``wait_s``."""
    sub = Submission(cid=_cid(client), kind=kind, target=target, caused_by=caused_by, payload=payload)
    a = _send(Path(codoc_dir), client, [sub], root_dir, wait_s)[0]
    return {"status": a["status"], "refused": [a["conflict"]] if a["conflict"] else [], "ops": a["ops"]}


def propose(codoc_dir: str | Path, *, client: str, pid: str, kind: str, target: str | None, note: str, build: bool,
            bodies: list[dict], rewrites: list[dict], strikes: list[str], caused_by: str | None = None,
            root_dir: str | None = None, wait_s: float = 10.0) -> dict:
    """A machine's proposal as plan ``pid``, decided whole in the applier
    (``Workspace.propose``): a plan nothing landed under is taken back there,
    whether or not the ack arrives in time. ``rewrites`` are
    ``{bid, path, new_md, base_text}``."""
    return _decide(codoc_dir, client, "propose", {"pid": pid}, {
        "kind": kind, "target": target, "note": note, "build": build, "bodies": bodies, "rewrites": rewrites,
        "strikes": strikes}, caused_by, root_dir, wait_s)


def amend(codoc_dir: str | Path, *, client: str, fid: str, rewrites: list[dict], bodies: list[dict] | None = None,
          strikes: list[str] | None = None, structural: bool = False, note: str = "", caused_by: str | None = None,
          root_dir: str | None = None, wait_s: float = 10.0) -> dict:
    """A machine's repair of feature ``fid``, decided once in the applier
    (``Workspace.amend``). ``rewrites`` are ``{bid, path, new_md, base_text}``."""
    return _decide(codoc_dir, client, "amend", {"fid": fid}, {
        "rewrites": rewrites, "bodies": bodies or [], "strikes": strikes or [], "structural": structural,
        "note": note}, caused_by, root_dir, wait_s)


if __name__ == "__main__":
    import sys
    settle_closed_epoch(sys.argv[1])
