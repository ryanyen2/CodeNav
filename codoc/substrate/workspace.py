"""``Workspace``: the daemon-facing façade over the calculus (spec §5, §7).

One process opens a workspace; it is the applier. Opening replays the logs
(snapshot first, then every op outside the snapshot's frontier, in
``(at, session, seq)`` order), advances the clock past everything replayed, and
starts a new session log. ``submit`` mints ids, stamps actors, applies, and
appends to the session log; ``ingest`` runs the boundary diff and submits its
ops as one batch under the gate the caller states. ``drain_inbox`` turns client
submissions into ops and acks them by client id (idempotent).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.ids import new_batch_id, new_plan_id, new_session_id, op_id
from codoc.calculus.ingest import Ingest, as_plan_bodies, ingest, resolve_pending_refs
from codoc.calculus.ops import Op
from codoc.calculus.store import Policy, Store
from codoc.substrate import log as L
from codoc.substrate.clock import Clock, SkewError

OWNER = "owner"


@dataclass
class Roles:
    """Actor → role. The local checkout's person is ``owner``; everyone else
    defaults to ``suggest`` unless listed. The hub fills this from GitHub."""
    table: dict[str, str] = field(default_factory=dict)
    default: str = "suggest"

    def role(self, actor: str) -> str:
        if actor in self.table:
            return self.table[actor]
        if actor in ("loop",) or actor.startswith(("translator:", "import", "agent:")):
            return "machine"
        return self.default

    def policy(self, op: Op) -> str | None:
        role = self.role(op.actor)
        if role in (OWNER, "maintainer", "machine"):
            return None
        # suggest: plans, comments, and ops inside a plan only.
        if op.kind == K.PLAN_OPEN or op.plan:
            return None
        return f"{role} may only write plans"


class Workspace:
    def __init__(self, codoc_dir: str | Path, *, node: str = "local", roles: Roles | None = None,
                 session: str | None = None, readonly: bool = False) -> None:
        self.codoc_dir = Path(codoc_dir)
        self.node = node
        # The local person owns the checkout; a v1 import runs only on that
        # person's machine and writes in their name.
        self.roles = roles or Roles({"human:local": OWNER, "human:import": OWNER})
        self.clock = Clock(node)
        self.store = Store(policy=self.roles.policy)
        self.problems: list[L.LogProblem] = []
        self.quarantined: list[Op] = []
        self._inbox_offsets: dict[str, int] = {}
        self.rebuild()
        self.readonly = readonly
        self.session = session or new_session_id()
        self.seq = 0
        # A read-only open (an MCP read, `codoc status`) replays the logs and
        # writes nothing: no session file, and `submit` refuses.
        self.writer = None if readonly else L.SessionWriter(self.codoc_dir, self.session, node, self.clock.tick())

    # ── replay ─────────────────────────────────────────────────────────────
    def rebuild(self) -> None:
        self.store = Store(policy=self.roles.policy)
        self.problems = []
        files = []
        for p in L.list_sessions(self.codoc_dir):
            f = L.read_session(p)
            self.problems.extend(f.problems)
            if f.valid:
                files.append(f)
        exclude = None
        snaps = L.list_snapshots(self.codoc_dir)
        if snaps:
            snap = L.read_session(snaps[-1])
            self.problems.extend(snap.problems)
            if snap.valid:
                exclude = snap.header.get("frontier") or {}
                for op in snap.ops:
                    self._replay_one(op)
        for op in L.replay_order(files, exclude):
            self._replay_one(op)

    def _replay_one(self, op: Op) -> None:
        try:
            self.clock.observe(op.at)
        except SkewError:
            self.quarantined.append(op)
            return
        # Replay applies the op as recorded: the wire payload is regenerated
        # from the canonical one where they differ, so the applier recomputes
        # the same thing; conflicts recorded before are recorded again.
        apply(self.store, _wire(op))

    # ── writing ────────────────────────────────────────────────────────────
    def _mint(self, kind: str, target: dict, payload: dict, *, actor: str, batch: str,
              plan: str | None = None, base: dict | None = None, caused_by: str | None = None) -> Op:
        self.seq += 1
        return Op(id=op_id(self.session, self.seq), at=self.clock.tick(), actor=actor, batch=batch,
                  kind=kind, target=dict(target), payload=dict(payload), base=dict(base or {}),
                  plan=plan, caused_by=caused_by)

    def submit(self, bodies: list[dict], *, actor: str, batch: str | None = None, plan: str | None = None,
               base: dict | None = None, caused_by: str | None = None) -> list[Op]:
        """Mint, apply and log a batch of op bodies ``{kind, target, payload}``."""
        if self.writer is None:
            raise RuntimeError("read-only workspace: submit through the daemon's inbox")
        batch = batch or new_batch_id()
        out: list[Op] = []
        for b in bodies:
            op = self._mint(b["kind"], b.get("target", {}), b.get("payload", {}), actor=actor, batch=batch,
                            plan=b.get("plan", plan), base=base, caused_by=b.get("caused_by", caused_by))
            apply(self.store, op)
            self.writer.append(op)
            out.append(op)
        return out

    def open_plan(self, *, actor: str, kind: str = "edit", target: str | None = None, note: str = "",
                  pid: str | None = None) -> str:
        pid = pid or new_plan_id()
        self.submit([{"kind": K.PLAN_OPEN, "target": {"pid": pid},
                      "payload": {"kind": kind, "target": target, "note": note}}], actor=actor, batch=pid)
        return pid

    def ingest(self, bid: str, path: str, *, base_text: str, new_md: str, actor: str, base: dict | None = None,
               plan: str | None = None, caused_by: str | None = None, batch: str | None = None,
               plan_note: str = "", gate: float | None = None) -> tuple[Ingest, list[Op]]:
        """Run the boundary diff and submit its ops as one batch. ``gate`` is the
        ``deleted_fraction`` above which a trunk write becomes a plan; ``None``
        means never. A demotion (edited since base, or over the gate) opens a
        plan with this actor and tags every op with it."""
        block = self.store.blocks.get(bid)
        if block is None:
            return Ingest(refused="no_block"), []
        text = block.text(path)
        res = ingest(text, bid=bid, path=path, base_text=base_text, new_md=new_md, actor=actor,
                     base=base if base is not None else dict(self.store.frontier), plan=plan)
        if res.refused:
            return res, []
        if plan is None and (res.demote_to_plan or (gate is not None and res.deleted_fraction > gate)):
            reason = res.demote_to_plan or "over_gate"
            plan = self.open_plan(actor=actor, kind="edit", target=bid, note=plan_note or reason)
            res.demote_to_plan = reason
        batch = batch or plan or new_batch_id()
        bodies = as_plan_bodies(res.bodies, plan) if plan else res.bodies
        ops: list[Op] = []
        insert_ids: list[str] = []
        pending_marks: list[dict] = []
        for b in bodies:
            if b["kind"] == K.TEXT_MARK and any(isinstance(b["payload"].get(k), list) and b["payload"][k][:1] == ["$ins"]
                                                 for k in ("start", "end")):
                pending_marks.append(b)
                continue
            op = self._mint(b["kind"], b["target"], b["payload"], actor=actor, batch=batch, plan=plan,
                            base=base, caused_by=caused_by)
            if b["kind"] == K.TEXT_INSERT:
                insert_ids.append(op.id)
            apply(self.store, op)
            self.writer.append(op)
            ops.append(op)
        if pending_marks:
            resolve_pending_refs(pending_marks, insert_ids)
            for b in pending_marks:
                op = self._mint(b["kind"], b["target"], b["payload"], actor=actor, batch=batch, plan=plan,
                                base=base, caused_by=caused_by)
                apply(self.store, op)
                self.writer.append(op)
                ops.append(op)
        return res, ops

    # ── clients ────────────────────────────────────────────────────────────
    def drain_inbox(self, identity: dict[str, str]) -> list[Op]:
        """Apply every new submission from every client. ``identity`` maps a
        client name to the actor the daemon stamps; unknown clients are
        ignored (a file nobody vouches for writes nothing)."""
        out: list[Op] = []
        acked = self._acked_cids()
        for client in L.clients(self.codoc_dir):
            actor = identity.get(client)
            if actor is None:
                continue
            subs, end = L.read_inbox(self.codoc_dir, client, self._inbox_offsets.get(client, 0))
            self._inbox_offsets[client] = end
            for s in subs:
                if s.cid in acked.get(client, set()):
                    continue
                if s.kind == "ingest":
                    # A whole-text write from another process: the boundary diff
                    # runs here, in the applier. The ack names the first op.
                    res, ops = self.ingest(s.target["bid"], s.target.get("path", "text"),
                                           base_text=s.payload.get("base_text", ""), new_md=s.payload.get("new_md", ""),
                                           actor=actor, plan=s.plan, caused_by=s.caused_by, batch=s.batch or None)
                    first = ops[0] if ops else Op(id="", at="", actor=actor, batch="", kind="ingest",
                                                  conflict=res.refused or ("empty" if not ops else None))
                    L.ack(self.codoc_dir, client, s.cid, first)
                    acked.setdefault(client, set()).add(s.cid)
                    out.extend(ops)
                    continue
                op = self._mint(s.kind, s.target, s.payload, actor=actor, batch=s.batch or new_batch_id(),
                                plan=s.plan, base=s.base, caused_by=s.caused_by)
                apply(self.store, op)
                self.writer.append(op)
                L.ack(self.codoc_dir, client, s.cid, op)
                acked.setdefault(client, set()).add(s.cid)
                out.append(op)
        return out

    def _acked_cids(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        d = L.inbox_dir(self.codoc_dir)
        if not d.exists():
            return out
        for p in d.glob("*.ack.jsonl"):
            client = p.name[: -len(".ack.jsonl")]
            with p.open("r", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        out.setdefault(client, set()).add(json.loads(line)["cid"])
                    except (json.JSONDecodeError, KeyError):
                        continue
        return out

    # ── sessions ───────────────────────────────────────────────────────────
    def rotate_session(self) -> str:
        """Close the current session log and open a new one (on commit, HEAD
        change, or at the caller's request)."""
        if self.writer is None:
            return self.session
        self.writer.close()
        self.session = new_session_id()
        self.seq = 0
        self.writer = L.SessionWriter(self.codoc_dir, self.session, self.node, self.clock.tick())
        return self.session

    def close(self) -> None:
        if self.writer is not None:
            self.writer.close()


def _wire(op: Op) -> Op:
    """The op as a client would have sent it, from its recorded form. The
    canonical payload is a superset of the wire payload for every kind, and
    the applier accepts canonical forms directly, so replaying the recorded op
    reproduces the recorded state (I6)."""
    return Op(id=op.id, at=op.at, actor=op.actor, batch=op.batch, kind=op.kind,
              target=dict(op.target), payload=dict(op.payload), base=dict(op.base),
              plan=op.plan, caused_by=op.caused_by)
