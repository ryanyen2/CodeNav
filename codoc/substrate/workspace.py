"""``Workspace``: the daemon-facing façade over the calculus (spec §5, §7).

One process opens a workspace; it is the applier. Opening replays the logs
(snapshot first, then every op outside the snapshot's frontier, in
``(at, session, seq)`` order), advances the clock past everything replayed, and
starts a new session log. ``submit`` mints ids, stamps actors, applies, and
appends to the session log; ``ingest`` runs the boundary diff and submits its
ops as one batch under the gate the caller states. ``drain_inbox`` turns client
submissions into ops and acks them by client id (idempotent).

Batches are not atomic: an op that conflicts is recorded and the rest of the
batch still applies (the loop relies on this: a plan whose third paragraph
fails to ingest keeps its first two). A caller that needs all-or-nothing checks
its bodies before submitting.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.doc import HEADING, PARAGRAPH, is_text_field
from codoc.calculus.ids import actor_kind, new_batch_id, new_plan_id, new_session_id, op_id
from codoc.calculus.ingest import Ingest, as_plan_bodies, ingest, resolve_pending_refs
from codoc.calculus.ops import Op
from codoc.calculus.store import Store
from codoc.substrate import log as L
from codoc.substrate.clock import Clock

OWNER = "owner"
MACHINE_KINDS = frozenset({"agent", "translator", "loop", "import"})


@dataclass
class Roles:
    """Actor → role, and the policy the applier runs (spec §7.3).

    - ``owner`` / ``maintainer``: everything.
    - ``machine`` (agents, the loop, translators, imports): the trunk and plans,
      but no verdicts and no hand-offs; ``feature.realized`` only from the loop
      itself, which is the daemon settling evidence.
    - ``suggest`` (everyone else): ``plan.open`` and ops tagged with a plan the
      same actor opened. Riding someone else's open plan would land a stranger's
      words when the maintainer accepts it.
    """
    table: dict[str, str] = field(default_factory=dict)
    default: str = "suggest"

    @classmethod
    def load(cls, codoc_dir: str | Path) -> "Roles":
        """The table every clone must agree on lives in the tracked
        ``config.json`` (``roles: {actor: role}``); the local person and a v1
        import are owners unless it says otherwise."""
        from codoc.doclang import read_config
        cfg = read_config(codoc_dir).get("roles")
        table = {"human:local": OWNER, "human:import": OWNER}
        if isinstance(cfg, dict):
            table.update({str(k): str(v) for k, v in cfg.items()})
        return cls(table)

    def role(self, actor: str) -> str:
        if actor in self.table:
            return self.table[actor]
        if actor_kind(actor) in MACHINE_KINDS:
            return "machine"
        return self.default

    def policy(self, store: Store, op: Op) -> str | None:
        role = self.role(op.actor)
        if role in (OWNER, "maintainer"):
            return None
        if role == "machine":
            if op.kind in K.VERDICTS and not (op.kind == K.FEATURE_REALIZED and op.actor == "loop"):
                return f"{op.actor} may not write {op.kind}"
            return None
        if op.kind == K.PLAN_OPEN:
            return None
        if op.plan:
            plan = store.plans.get(op.plan)
            if plan is not None and plan.actor == op.actor:
                return None
            return f"{role} may only write to its own plans"
        return f"{role} may only write plans"


class Workspace:
    def __init__(self, codoc_dir: str | Path, *, node: str = "local", roles: Roles | None = None,
                 session: str | None = None, readonly: bool = False) -> None:
        self.codoc_dir = Path(codoc_dir)
        self.node = node
        self.roles = roles or Roles.load(self.codoc_dir)
        self.clock = Clock(node)
        self.store = Store(policy=self.roles.policy)
        self.problems: list[L.LogProblem] = []
        self.torn: list[L.SessionFile] = []
        self._inbox_offsets: dict[str, int] = {}
        self._acked: dict[str, dict[str, str]] = L.acked(self.codoc_dir)   # client → cid → op id
        self.rebuild()
        self.readonly = readonly
        self.session = session or new_session_id()
        self.seq = 0
        # A read-only open (an MCP read, `codoc status`) replays the logs and
        # writes nothing: no session file, and `submit` refuses.
        self.writer = None if readonly else L.SessionWriter(self.codoc_dir, self.session, node, self.clock.tick())

    # ── replay ─────────────────────────────────────────────────────────────
    def rebuild(self) -> None:
        """Fold every valid log on disk into a fresh store (I6). The log is the
        truth: nothing replayed is refused, and the clock advances past it."""
        self.store = Store(policy=self.roles.policy)
        self.problems = []
        self.torn = []
        files = []
        for p in L.list_sessions(self.codoc_dir):
            f = L.read_session(p)
            self.problems.extend(f.problems)
            if f.torn_tail is not None:
                self.torn.append(f)
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
        self.clock.observe(op.at)
        # The applier accepts every canonical payload back, so replaying the
        # recorded op reproduces the recorded state; conflicts recorded before
        # are recorded again.
        apply(self.store, _wire(op))

    # ── writing ────────────────────────────────────────────────────────────
    def _mint(self, kind: str, target: dict, payload: dict, *, actor: str, batch: str,
              plan: str | None = None, base: dict | None = None, caused_by: str | None = None) -> Op:
        self.seq += 1
        return Op(id=op_id(self.session, self.seq), at=self.clock.tick(), actor=actor, batch=batch,
                  kind=kind, target=dict(target), payload=dict(payload), base=dict(base or {}),
                  plan=plan, caused_by=caused_by)

    def _apply(self, op: Op) -> Op:
        if self.writer is None:
            raise RuntimeError("read-only workspace: submit through the daemon's inbox")
        apply(self.store, op)
        self.writer.append(op)
        return op

    def submit(self, bodies: list[dict], *, actor: str, batch: str | None = None, plan: str | None = None,
               base: dict | None = None, caused_by: str | None = None) -> list[Op]:
        """Mint, apply and log a batch of op bodies ``{kind, target, payload}``
        (see the module note on atomicity)."""
        batch = batch or new_batch_id()
        out = [self._apply(self._mint(b["kind"], b.get("target", {}), b.get("payload", {}), actor=actor, batch=batch,
                                      plan=b.get("plan", plan), base=base, caused_by=b.get("caused_by", caused_by)))
               for b in bodies]
        self._sync()
        return out

    def open_plan(self, *, actor: str, kind: str = "edit", target: str | None = None, note: str = "",
                  pid: str | None = None) -> str:
        pid = pid or new_plan_id()
        self.submit([{"kind": K.PLAN_OPEN, "target": {"pid": pid},
                      "payload": {"kind": kind, "target": target, "note": note}}], actor=actor, batch=pid)
        return pid

    # ── ingest ─────────────────────────────────────────────────────────────
    def plan_ingest(self, bid: str, path: str, *, base_text: str, new_md: str, actor: str,
                    trunk: bool = True) -> Ingest:
        """The boundary diff, computed and not applied: what ``new_md`` would do
        to the field. ``trunk`` says the ops will land on the trunk, where a
        machine may not drop a person's mark; under a plan the person decides."""
        block = self.store.blocks.get(bid)
        if block is None:
            return Ingest(refused="no_block")
        if not block.live:
            return Ingest(refused="dead_block")
        if not is_text_field(block.kind, path):
            return Ingest(refused="not_text")
        return ingest(block.text(path), bid=bid, path=path, base_text=base_text, new_md=new_md, actor=actor,
                      trunk=trunk, multiline=block.kind not in (HEADING, PARAGRAPH))

    def commit_ingest(self, res: Ingest, *, actor: str, plan: str | None = None, batch: str | None = None,
                      base: dict | None = None, caused_by: str | None = None) -> list[Op]:
        """Mint and apply a computed ingest as one batch, under ``plan`` if
        given (its deletes become ``suggest_del`` marks). Every op records the
        frontier the diff ran at as ``base``."""
        if res.refused:
            return []
        bodies = as_plan_bodies(res.bodies, plan) if plan else res.bodies
        batch = batch or plan or new_batch_id()
        base = base if base is not None else dict(self.store.frontier)
        ops: list[Op] = []
        insert_ids: list[str] = []
        for b in bodies:
            body = {"kind": b["kind"], "target": b["target"], "payload": dict(b["payload"])}
            resolve_pending_refs([body], insert_ids)      # inserts precede the marks that name them
            op = self._apply(self._mint(body["kind"], body["target"], body["payload"], actor=actor, batch=batch,
                                        plan=plan, base=base, caused_by=caused_by))
            if b["kind"] == K.TEXT_INSERT:
                insert_ids.append(op.id)
            ops.append(op)
        self._sync()
        return ops

    def ingest(self, bid: str, path: str, *, base_text: str, new_md: str, actor: str, base: dict | None = None,
               plan: str | None = None, caused_by: str | None = None, batch: str | None = None) -> tuple[Ingest, list[Op]]:
        """``plan_ingest`` then ``commit_ingest``, on the trunk or under ``plan``.
        The loop's gate (a large repair becomes a plan) lives in the loop,
        which sees the whole repair; this is one field."""
        res = self.plan_ingest(bid, path, base_text=base_text, new_md=new_md, actor=actor, trunk=plan is None)
        if res.refused:
            return res, []
        return res, self.commit_ingest(res, actor=actor, plan=plan, batch=batch, base=base, caused_by=caused_by)

    # ── clients ────────────────────────────────────────────────────────────
    def drain_inbox(self, identity=L.actor_for) -> list[Op]:
        """Apply every new submission from every client. ``identity`` maps a
        client name to the actor the daemon stamps (a callable or a dict);
        unknown clients are ignored (a file nobody vouches for writes nothing).
        Only whole lines are consumed; a malformed one is acked with an error so
        the client learns, and the offset advances line by line, after the ack.

        A client cannot know the op ids of characters it typed a moment ago and
        has not yet seen acked, so an anchor may name them provisionally as
        ``["$cid", "<client id>", k]``: the k-th character of the insert that
        submission minted. The drain resolves those against every ack it has
        ever given that client, so a chain of typing survives a drain boundary
        the client did not choose. No line can block the next: a submission
        that raises is acked with the error and left behind."""
        out: list[Op] = []
        for client in L.clients(self.codoc_dir):
            actor = identity(client) if callable(identity) else identity.get(client)
            if actor is None:
                continue
            acked = self._acked.setdefault(client, {})
            for line in L.read_inbox(self.codoc_dir, client, self._inbox_offsets.get(client, 0)):
                s = line.sub
                if s is None:
                    L.ack(self.codoc_dir, client, line.cid, error=line.error)
                elif s.cid not in acked:
                    try:
                        out.extend(self._drain_one(client, actor, s, acked))
                    except Exception as e:  # noqa: BLE001 — the applier is total; so is the drain
                        L.ack(self.codoc_dir, client, s.cid, error=f"refused: {e!r}")
                        acked[s.cid] = ""
                self._inbox_offsets[client] = line.end
        self._sync()
        return out

    def _drain_one(self, client: str, actor: str, s: L.Submission, acked: dict[str, str]) -> list[Op]:
        if s.kind == "ingest":
            # A whole-text write from another process: the boundary diff runs
            # here, in the applier. The ack names the first op.
            res, ops = self.ingest(s.target["bid"], s.target.get("path", "text"), base_text=s.payload["base_text"],
                                   new_md=s.payload["new_md"], actor=actor, plan=s.plan, caused_by=s.caused_by,
                                   batch=s.batch or None)
            L.ack(self.codoc_dir, client, s.cid, ops[0] if ops else None, error=res.refused or (None if ops else "unchanged"))
            acked[s.cid] = ops[0].id if ops else ""
            return ops
        op = self._apply(self._mint(s.kind, s.target, _resolve_provisional(s.payload, acked), actor=actor,
                                    batch=s.batch or new_batch_id(), plan=s.plan, base=s.base, caused_by=s.caused_by))
        L.ack(self.codoc_dir, client, s.cid, op)
        acked[s.cid] = op.id
        return [op]

    # ── sessions ───────────────────────────────────────────────────────────
    def _sync(self) -> None:
        if self.writer is not None:
            self.writer.sync()

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


def _resolve_provisional(payload: dict, minted: dict[str, str]) -> dict:
    """Replace ``["$cid", cid, k]`` anchors with ``[op_id, k]`` once the insert
    with that client id has been minted. An unresolvable one is left as is and
    fails as an unknown anchor, which is the honest outcome."""
    out = dict(payload)
    for key in ("after", "from", "to", "start", "end", "at"):
        v = out.get(key)
        if isinstance(v, list) and len(v) == 3 and v[0] == "$cid" and minted.get(v[1]):
            out[key] = [minted[v[1]], v[2]]
    if isinstance(out.get("segments"), list):
        out["segments"] = [[minted.get(seg[1], seg[0]) if isinstance(seg[0], str) and seg[0] == "$cid" else seg[0], *seg[1 + (seg[0] == "$cid"):]]
                           if isinstance(seg, list) and seg and seg[0] == "$cid" else seg for seg in out["segments"]]
    return out


def _wire(op: Op) -> Op:
    """The op as a client would have sent it, from its recorded form. The
    canonical payload is a superset of the wire payload for every kind, and
    the applier accepts canonical forms directly, so replaying the recorded op
    reproduces the recorded state (I6)."""
    return Op(id=op.id, at=op.at, actor=op.actor, batch=op.batch, kind=op.kind,
              target=dict(op.target), payload=dict(op.payload), base=dict(op.base),
              plan=op.plan, caused_by=op.caused_by)
