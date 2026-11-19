"""How a second process writes and reads: through the daemon's inbox, or as the
applier when no daemon owns the checkout.

The MCP server, the CLI and the hub are separate processes. Only one process
may apply (docs/concurrency.md, Processes), so a writer takes the applier lock
and asks whether a daemon is alive (the pidfile). If so it appends submissions
to its inbox and the daemon drains them on the next batch; the caller gets
client ids back and may poll the ack file. If not, this process is the
applier: it opens the workspace once and keeps it for the life of the process,
so a long-lived writer (the MCP server) writes one session log rather than one
per call. A daemon that appears later takes over at the next call, and the
held workspace is closed. Both ways a submission is applied by
``Workspace.apply_submissions``.

Two daemonless writers are allowed (the MCP server holds its workspace while
the person runs `codoc accept`), so ``applying()`` wraps apply-and-publish
in an exclusive lock, and both writes and reads rebuild when the log grew by a
hand that is not ours since our last turn. Without the lock each held a store
the other had moved on from and the derived files described whichever published
last; without the read half, the agent's `codoc_tree` and the `base_text`
its next amend cites describe the tree before the person's `codoc accept`.
The lock is per call, never for the life of the process: a lease would leave
the other writer submitting into an inbox with no daemon to drain it.

Reads for other processes are here too: ``queue_status`` (what is queued,
reported and edited) and ``read_history`` (one feature's ops and attribution).
"""
from __future__ import annotations

import atexit
import json
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.derive import attribution, dead_cites, history as feature_history, offered_plans, phase, title
from codoc.calculus.ids import new_batch_id
from codoc.calculus.ingest import resolve_pending_refs
from codoc.daemon.epoch import epoch_alive, read_activity
from codoc.daemon.pidfile import daemon_running
from codoc.daemon.publish import publish, read_status, realizing_is_fresh
from codoc.doclang import workspace_doc_language
from codoc.index.git import head
from codoc.loops.realize import queued, settle
from codoc.workspace import Workspace
from codoc.workspace import inbox as I
from codoc.workspace import log as L
from codoc.workspace.inbox import Submission
from codoc.workspace.layout import APPLIER_LOCK, locked


def _actor(client: str, codoc_dir: Path) -> str:
    """The actor a client writes as: one identity function for the inbox path
    and the applier path, so a contributor is the same actor on both."""
    actor = I.actor_for(client, codoc_dir)
    if actor is None:
        raise ValueError(f"no identity for client {client!r}")
    return actor


def settle_if_idle(ws: Workspace, res=None, *, sha: str, now: float | None = None) -> list:
    """Settle the hand-offs that binding ops now cite (``realize.settle``),
    unless an agent epoch is open: its build is in progress, so the feature
    stays queued and the agent's amends land as plans. The batch, or the
    process, that sees the epoch close runs the same check. Every applier
    settles through here."""
    if epoch_alive(ws.codoc_dir, now=now):
        return []
    return settle(ws, res, sha=sha)


# Held by an apply and by `release`. The MCP server applies in FastMCP's
# worker threads and releases from its signal handler in the main thread, so a
# stop waits for the batch in flight instead of closing the log under it.
# Reentrant: `applying` releases a workspace when a daemon takes the checkout.
_LOCK = threading.RLock()
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
    turn. Both the writes and the reads go through this: a held workspace that
    has not looked describes the tree before the person's `codoc accept`, and
    the agent's next amend computes its `base_text` from that."""
    mine = ws.session + ".jsonl"
    was = _SEEN.get(codoc_dir)
    if was is None:
        return
    now = _sizes(codoc_dir)
    if {k: v for k, v in now.items() if k != mine} != {k: v for k, v in was.items() if k != mine}:
        ws.rebuild()


class DaemonRunning(RuntimeError):
    """A daemon owns the checkout: submit to its inbox."""


@contextmanager
def applying(codoc_dir: Path):
    """The workspace, under the applier lock, caught up with ``HEAD``, with
    the session file on disk and with anybody else's writes. Yields inside the
    lock so apply and publish are one turn. Raises ``DaemonRunning`` when a
    daemon owns the checkout, asked under the lock: a daemon that started while
    this process waited for the lock rebuilt without what this process would
    apply now, and never reads it."""
    with _LOCK, locked(codoc_dir / APPLIER_LOCK):
        if daemon_running(codoc_dir):
            release(codoc_dir)
            raise DaemonRunning(str(codoc_dir))
        ws = _applier(codoc_dir)
        if not ws.follow_head(*head(codoc_dir.parent)):
            _catch_up(codoc_dir, ws)
        yield ws
        _SEEN[codoc_dir] = _sizes(codoc_dir)


def release(codoc_dir: Path | None = None) -> None:
    """Close the workspaces this process holds (at exit, or when a daemon
    takes the checkout back), after any apply in flight."""
    with _LOCK:
        for key in ([codoc_dir] if codoc_dir is not None else list(_OWNED)):
            ws = _OWNED.pop(Path(key), None)
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
        if b["kind"] == K.TEXT_INSERT:
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
            got = ws.apply_submissions([(client, actor, s) for s in subs])
            settle_if_idle(ws, sha=ws.head[0])
            publish(codoc_dir, ws.store, root_dir=root_dir or str(codoc_dir.parent))
    except DaemonRunning:
        I.submit(codoc_dir, client, subs)
        return [{**a, "ops": [a["op"]] if a["op"] else []}
                for a in wait_for_acks(codoc_dir, client, [s.cid for s in subs], wait_s)]
    return [{"cid": s.cid, "op": a.ops[0].id if a.ops else None, "conflict": a.conflict, "status": a.status,
             "ops": [o.id for o in a.ops]} for s, a in zip(subs, got)]


def settle_closed_epoch(codoc_dir: str | Path) -> None:
    """Settle, once, what could not settle while an agent epoch was open, when
    no daemon owns the checkout. Every MCP write of a Claude Code turn lands while its epoch is
    open, so ``_send`` settles nothing, and with no daemon nothing applies after
    the Stop hook closes it. The hook starts this in a detached process
    (``python -m codoc.daemon.client <codoc_dir>``). An epoch the next turn has
    already reopened is left to that turn's own close."""
    codoc_dir = Path(codoc_dir)
    try:
        with applying(codoc_dir) as ws:
            if settle_if_idle(ws, sha=ws.head[0]):
                publish(codoc_dir, ws.store, root_dir=str(codoc_dir.parent))
    except DaemonRunning:
        pass                     # the daemon's batch that sees the epoch close settles it


def wait_for_acks(codoc_dir: Path, client: str, cids: list[str], wait_s: float) -> list[dict]:
    path = I.inbox_dir(codoc_dir) / f"{client}.ack.jsonl"
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
    # what it saw: `_SEEN` is the write turn's memory, and a read must not
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


# ── reads ────────────────────────────────────────────────────────────────────

def doc_language_block(codoc_dir: str | Path) -> dict:
    """The language the tree is authored in, as a reader is told it."""
    lang = workspace_doc_language(codoc_dir)
    block = {"code": lang.code, "name": lang.name}
    if not lang.is_default:
        block["instruction"] = (f"This tree is authored in {lang.name}. Write every title, paragraph and note in "
                                f"{lang.name}; never translate identifiers, paths or codoc: targets.")
    return block


def queue_status(codoc_dir: str | Path) -> dict:
    """Where the tree stands for its builder. ``queued_list`` is what is left
    to build; ``reported_list`` is what this turn's calls cited
    (``epoch.note_cited``), which settles when the turn ends. State
    ``reported`` says every queued hand-off is reported."""
    store = read(codoc_dir).store
    heads = store.headings()
    phases = {h.id: phase(store, h.id) for h in heads}
    cited = set(read_activity(codoc_dir).get("cited") or [])
    items: dict[bool, list] = {False: [], True: []}
    for f in queued(store):
        did = (store.watermarks(f).handoffs or [""])[-1]
        items[did in cited].append({"feature_id": f, "title": title(store, f, "rejected"), "handoff": f"d:{did}"})
    todo, reported = items[False], items[True]
    state = read_status(codoc_dir).get("state", "synced")
    if state == "realizing" and not realizing_is_fresh(codoc_dir):
        # The lease lapsed, or it is the lease of the pass this agent belongs to:
        # either way no other pass is working the queue.
        state = "queued" if todo or reported else "synced"
    if state == "queued" and not todo:
        state = "reported"
    dead = [{"block": bid, "symbol": m.attrs.get("symbol")} for bid, m in dead_cites(store)]
    edited = [f for f, p in phases.items() if p == "edited"]
    return {"ok": True, "state": state, "features": len(heads), "queued": len(todo), "queued_list": todo[:20],
            "reported": len(reported), "reported_list": reported[:20],
            "edited": len(edited), "edited_list": [{"feature_id": f, "title": title(store, f)} for f in edited[:20]],
            "plans": len(offered_plans(store)),
            "dead_cites": len(dead), "dead_cite_list": dead[:20],
            "doc_language": doc_language_block(codoc_dir)}


def read_history(codoc_dir: str | Path, feature_id: str, limit: int = 20) -> dict:
    """One feature's last ``limit`` ops and who wrote each span of its body."""
    store = read(codoc_dir).store
    if feature_id not in store.blocks:
        return {"ok": False, "error": f"no feature {feature_id!r}"}
    entries = []
    for o in feature_history(store, feature_id)[-limit:]:
        e = {"op": o.id, "at": o.at, "actor": o.actor, "kind": o.kind, "batch": o.batch}
        if o.caused_by:
            e["caused_by"] = o.caused_by
        if o.plan:
            e["plan"] = o.plan
        if o.kind == K.TEXT_INSERT:
            e["text"] = o.payload.get("text")
        if o.kind == K.TEXT_DELETE:
            e["removed_segments"] = o.payload.get("segments")
        entries.append(e)
    blame = {(b.id if f == "text" else f"{b.id}.{f}"):
             [{"actor": r.actor, "start": r.start, "end": r.end} for r in attribution(b.text(f))]
             for b in store.body(feature_id) for f in b.layout.text}
    return {"ok": True, "feature_id": feature_id, "title": title(store, feature_id), "ops": entries, "attribution": blame}


if __name__ == "__main__":
    import sys
    settle_closed_epoch(sys.argv[1])
