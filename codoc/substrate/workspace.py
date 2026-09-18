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
from dataclasses import dataclass, field, replace
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.apply import apply, replay_recorded
from codoc.calculus.derive import GATE_HUMAN, GATE_MACHINE, HELD, block_written_by, phase
from codoc.calculus.doc import HEADING, PARAGRAPH, is_text_field
from codoc.calculus.ids import MACHINE_KINDS, actor_kind, new_batch_id, new_plan_id, new_session_id, op_id
from codoc.calculus.ingest import Ingest, as_plan_bodies, ingest, resolve_pending_refs
from codoc.calculus.ops import Op, frontier_union
from codoc.calculus.store import Store
from codoc.calculus.text import SUGGEST_DEL, AnchorError, nfc
from codoc.substrate import log as L
from codoc.substrate.clock import Clock

OWNER = "owner"
SUGGEST = "suggest"
NONE = "none"                  # recorded on revocation: not a role, the absence of one
COMMENT = "comment"
# The ops a comment is made of, besides the `block.add` that mints it.
_COMMENT_EDITS = frozenset({K.TEXT_INSERT, K.TEXT_DELETE, K.TEXT_MARK, K.BLOCK_SET})


def _withdrawing_own_empty_plan(store: Store, op: Op) -> bool:
    """Taking back a proposal of your own that nobody has written into is not a
    verdict on anybody's work: there is nothing to decide. A plan opened for a
    submission that then did not land would otherwise stand for ever, since
    ``plan.open`` has no inverse and only an owner may reject."""
    if op.kind != K.PLAN_REJECT:
        return False
    plan = store.plans.get(str(op.target.get("pid") or ""))
    return plan is not None and plan.actor == op.actor and plan.status == "open" and not plan.ops


def deleted_ops(store: Store, target: dict, payload: dict) -> set[str]:
    """The ops that minted the characters a delete names. The ``segments`` form
    lists them; the ``from``/``to`` form has to be resolved against the text,
    and resolves to nothing when the range does not land — the applier is where
    that is reported, not here."""
    if "segments" in payload:
        return {str(s[0]) for s in payload["segments"] or ()}
    try:
        start, end = _ref(payload["from"]), _ref(payload["to"])
        block, path = store.locate_char(start, (str(target.get("bid") or ""), str(target.get("path") or "text")))
        return block.text(path).ops_between(start, end)
    except (AnchorError, KeyError, IndexError, TypeError, ValueError):
        return set()


def _ref(v) -> tuple[str, int]:
    return (str(v[0]), int(v[1]))


def _own_plan_words(store: Store, actor: str, minted_by: set[str]) -> bool:
    """Whether every one of these characters was put there by an open plan of
    ``actor``'s. Taking back words you proposed touches nothing of anyone
    else's, and without it a contributor cannot undo their own typing: the
    inverse of their plan-tagged insert is a bare ``text.delete``, which a plan
    cannot carry."""
    if not minted_by:
        return False
    for oid in minted_by:
        minted = store.by_id.get(oid)
        plan = store.plans.get(minted.plan) if minted is not None and minted.plan else None
        if plan is None or plan.actor != actor or plan.status != "open":
            return False
    return True


def _own_plan_text(store: Store, op: Op) -> bool:
    """A delete of nothing but their own proposed words, or a restore undoing a
    delete of their own.

    A restore is judged by the delete it undoes, not by who minted the
    characters: a maintainer removing a plan's words is an ordinary trunk
    delete — "the plan sees fewer of its words survive" (01 §3) — and a
    suggester putting them back would be overriding a trunk op, which §7.3
    gives them no right to do."""
    if op.kind == K.TEXT_RESTORE:
        undone = store.by_id.get(str(op.payload.get("by") or ""))
        return undone is not None and undone.actor == op.actor
    if op.kind != K.TEXT_DELETE:
        return False
    return _own_plan_words(store, op.actor, deleted_ops(store, op.target, op.payload))


def is_comment_op(store: Store, kind: str, target: dict, payload: dict) -> bool:
    """Whether this op is part of writing a comment. A comment hangs off words
    rather than standing among them, so 02 §3.6 lets a suggester write one on
    the trunk: there is nothing for a maintainer to accept, only a thread to
    answer. Everything else they write becomes a plan of theirs."""
    if kind == K.BLOCK_ADD:
        return payload.get("kind") == COMMENT
    if kind not in _COMMENT_EDITS:
        return False
    if kind == K.TEXT_MARK and payload.get("kind") == COMMENT:
        return True            # the anchor mark, which lives on the prose
    b = store.blocks.get(str(target.get("bid") or ""))
    return b is not None and b.kind == COMMENT


def minted_by(store: Store, bid: str) -> str:
    """The actor whose ``block.add`` put this block there."""
    for o in store.ops_on([bid]):
        if o.kind == K.BLOCK_ADD and o.conflict is None:
            return o.actor
    return ""


def _own_comment(store: Store, op: Op) -> bool:
    """Whether a contributor writing inside a comment is writing their own.

    Editing INSIDE somebody's thread should mean replying in it. A reply is a
    new comment block, so adding one is always free, and so is writing into one
    they minted — the words they are still typing. Deleting another person's
    words is not, and neither is resolving a thread they did not start:
    ``status`` is what the margin and ``Build it`` read, so marking a
    maintainer's request-for-work done is answering it on their behalf."""
    if op.kind == K.BLOCK_ADD:
        return True
    bid = str(op.target.get("bid") or "")
    if op.kind == K.TEXT_MARK and op.payload.get("kind") == COMMENT:
        return True            # the anchor mark on the prose, which is theirs to place
    b = store.blocks.get(bid)
    if b is None:
        return False
    if op.kind == K.BLOCK_SET and op.target.get("path") == "status":
        return minted_by(store, str(b.content.get("thread") or bid)) == op.actor
    return minted_by(store, bid) == op.actor


@dataclass
class Roles:
    """Actor → role, and the policy the applier runs (spec §7.3).

    - ``owner`` / ``maintainer``: everything.
    - ``machine`` (agents, the loop, translators, imports): the trunk and plans,
      but no verdicts and no hand-offs; ``feature.realized`` only from the loop
      itself, which is the daemon settling evidence.
    - ``suggest`` (everyone else): ``plan.open``, ops tagged with a plan the
      same actor opened, and comments. Riding someone else's open plan would
      land a stranger's words when the maintainer accepts it. "Everyone else"
      is by the actor's KIND, so an actor the grammar does not name
      (``unknown:x``) is a suggester, not a machine. A contributor does not
      have to tag anything themselves: the drain opens the plan and tags their
      ops, turning a deletion into the plan's ``suggest_del`` (02 §3.6). What
      it cannot carry under a plan — a merge, a block delete — stays refused.
      Two untagged writes are allowed, because no plan can carry them and both
      are about their OWN words: a ``text.delete`` covering only characters an
      open plan of theirs minted, and a ``text.restore`` undoing a
      ``text.delete`` of their own (``_own_plan_text``).

    A comment is writable untagged by anyone: it hangs off words rather than
    standing among them, and there is nothing for a maintainer to accept. What
    a contributor may write there is their OWN — a new comment (which is what a
    reply is) and the words of one they minted. Deleting somebody else's words,
    or resolving a thread they did not start, is answering for them.
    """
    table: dict[str, str] = field(default_factory=dict)
    default: str = "suggest"

    @classmethod
    def load(cls, codoc_dir: str | Path) -> "Roles":
        """The table every clone must agree on lives in the tracked
        ``config.json`` (``roles: {actor: role}``); the local person and a v1
        import are owners unless it says otherwise."""
        from codoc.doclang import read_config
        return cls.from_config(read_config(codoc_dir))

    @classmethod
    def from_config(cls, cfg: dict) -> "Roles":
        """The same table from a ``config.json`` already in hand, so a caller
        that has read it does not read it twice or build the defaults again."""
        table = {"human:local": OWNER, "human:import": OWNER}
        roles = cfg.get("roles")
        if isinstance(roles, dict):
            table.update({str(k): str(v) for k, v in roles.items()})
        return cls(table)

    def role(self, actor: str) -> str:
        if actor in self.table:
            return self.table[actor]
        if actor_kind(actor) in MACHINE_KINDS:
            return "machine"
        return self.default

    def policy(self, store: Store, op: Op) -> str | None:
        role = self.role(op.actor)
        if role == NONE:
            # The table says this person was REMOVED. Their session is gone, but
            # an inbox line drained after the revocation and a clone validating
            # their pull request's logs both come through here, and "everyone
            # else" is a suggester — which would make the record of removal
            # grant exactly what a contributor has.
            return f"{op.actor} has no access"
        if role in (OWNER, "maintainer"):
            return None
        if role == "machine":
            if op.kind in K.VERDICTS and not (op.kind == K.FEATURE_REALIZED and op.actor == "loop") \
                    and not _withdrawing_own_empty_plan(store, op):
                return f"{op.actor} may not write {op.kind}"
            return None
        if op.kind == K.PLAN_OPEN:
            return None
        if _withdrawing_own_empty_plan(store, op) or _own_plan_text(store, op):
            return None
        if is_comment_op(store, op.kind, op.target, op.payload):
            return None if _own_comment(store, op) else f"{role} may not change another person's comment"
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
        # (actor, batch) → the plan this workspace opened for it: a
        # contributor's trunk edit AND a machine write the gate demoted.
        self._opened_plans: dict[tuple[str, str], str] = {}
        self._inbox_offsets: dict[str, int] = L.read_offsets(self.codoc_dir)
        if not readonly:
            L.rotate_acks(self.codoc_dir)
        self._acked: dict[str, dict[str, str]] = L.acked(self.codoc_dir)   # client → cid → op id
        self._sent_text: dict[str, str] = {}      # cid → the insert's text as the client counted it
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
        if len(snaps) > 1:
            # Two clones each folded and the merge brought both. Only one state
            # can be the state, and the other's log is in `log/archive/`, which
            # nothing reads — so say so, and read the archive, rather than
            # losing a feature quietly (04 §5: fold on the integration branch).
            self.problems.append(L.LogProblem(str(snaps[-1]), 1, (
                f"{len(snaps)} snapshots: {', '.join(p.name for p in snaps[:-1])} was folded by another clone; "
                "replaying its archived log. Re-fold once, on one branch.")))
            for p in L.list_archived(self.codoc_dir):
                f = L.read_session(p)
                if f.valid and not f.header.get("snapshot"):
                    files.append(f)
        if snaps:
            snap = L.read_session(snaps[-1])
            self.problems.extend(snap.problems)
            if snap.valid:
                exclude = snap.header.get("frontier") or {}
                for op in snap.ops:
                    self._replay_one(op)
                # The folded ops ARE in this state, so the frontier has to say
                # so. A frontier is a vector clock and a fold drops the folded
                # sessions' entries from it; without this, everything a later
                # watermark should cover reads as uncovered — a hand-off given
                # after a fold would not carry the words the person removed
                # before it, and `covered()` would call settled work in flight.
                self.store.frontier = frontier_union(self.store.frontier, exclude)
        for op in L.replay_order(files, exclude):
            self._replay_one(op)

    def _replay_one(self, op: Op) -> None:
        """Replay one recorded op, advancing the clock past it.
        ``apply.replay_recorded`` is the rule, and says why."""
        self.clock.observe(op.at)
        replay_recorded(self.store, op)

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
               plan: str | None = None, caused_by: str | None = None, batch: str | None = None,
               derived: bool = False) -> tuple[Ingest, list[Op]]:
        """``plan_ingest`` then ``commit_ingest``, on the trunk or under ``plan``.

        A machine writing prose on its own is gated HERE, because this is where
        every machine write passes — the loop's own pass, a translator, and the
        agent through ``codoc_reflect``. 01 §4 rules 5 and 8: a repair the trunk
        refuses because it would drop a mark the person placed, one that deletes
        more of the block than its gate allows, or any repair at all to a
        feature with pending intent, becomes a plan of that actor's. The
        document wins and the person decides. A caller that sees the whole
        repair and wants one plan for all of it (``reflect._amend``) computes
        and commits its parts itself, so it passes through here only once.

        ``derived`` says this content was READ OFF THE CODE rather than
        authored — the lift pass's re-derived diagram, and nothing else. The
        gate exists to keep a machine from overwriting words somebody meant,
        and a derived field holds none: it is attribution, like a binding
        refresh (03 §5.1). Gating it would make the always-on channel stop and
        ask whenever the code moved far enough, which is precisely when the
        picture most needs to follow."""
        res = self.plan_ingest(bid, path, base_text=base_text, new_md=new_md, actor=actor, trunk=plan is None)
        opened = None
        if plan is None and not derived and (why := self.must_propose(bid, actor, res)):
            plan = opened = self._plan_for(actor, batch or bid, bid, why)
            if plan is not None:
                batch = batch or plan
                res = self.plan_ingest(bid, path, base_text=base_text, new_md=new_md, actor=actor, trunk=False)
        if res.refused:
            self._withdraw_if_empty(opened)
            return res, []
        ops = self.commit_ingest(res, actor=actor, plan=plan, batch=batch, base=base, caused_by=caused_by)
        if not ops:
            self._withdraw_if_empty(opened)
        return res, ops

    def must_propose(self, bid: str, actor: str, res: Ingest) -> str | None:
        """Why this machine write may not land on the trunk, or None.

        Public because a caller that sees a whole repair asks it per block and
        opens ONE plan for all of them (``reflect._amend``). It is the only
        implementation of the rule: 01 §4 rule 7 puts the gate at one boundary,
        and a second copy is how two of them came to differ."""
        if actor_kind(actor) not in MACHINE_KINDS:
            return None
        if res.refused:
            return "it would drop a mark the person placed" if res.refused.startswith("would_drop_human_") else None
        if res.unchanged:
            return None
        fid = self.store.feature_of(bid)
        if fid and phase(self.store, fid) in HELD:
            return "the document has pending intent"
        gate = GATE_HUMAN if block_written_by(self.store, bid) == "human" else GATE_MACHINE
        return "a larger repair" if res.deleted_fraction > gate else None

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
            opened: set[str] = set()
            for line in L.read_inbox(self.codoc_dir, client, self._inbox_offsets.get(client, 0)):
                s = line.sub
                if s is None:
                    L.ack(self.codoc_dir, client, line.cid, error=line.error)
                elif s.cid not in acked:
                    before = set(self.store.plans)
                    try:
                        out.extend(self._drain_one(client, actor, s, acked))
                    except Exception as e:  # noqa: BLE001 — the applier is total; so is the drain
                        L.ack(self.codoc_dir, client, s.cid, error=f"refused: {e!r}")
                        acked[s.cid] = ""
                    opened |= set(self.store.plans) - before
                self._inbox_offsets[client] = line.end
            # A plan opened for a submission that then did not land is a verdict
            # the person would be asked to give on nothing.
            for pid in opened:
                self._withdraw_if_empty(pid)
        L.write_offsets(self.codoc_dir, self._inbox_offsets)
        self._sync()
        return out

    def _withdraw_if_empty(self, pid: str | None) -> None:
        """Take back a plan this workspace opened that nothing landed under: the
        editor would offer a verdict on nothing."""
        plan = self.store.plans.get(pid or "")
        if plan is None or plan.status != "open" or plan.ops:
            return
        self._apply(self._mint(K.PLAN_REJECT, {"pid": plan.id}, {}, actor=plan.actor, batch=plan.id))
        self._opened_plans = {k: v for k, v in self._opened_plans.items() if v != plan.id}

    def _as_proposal(self, actor: str, s: L.Submission) -> L.Submission:
        """A suggester's Backspace, as a proposal. A plan proposes and never
        kills (01 §3), so a range ``text.delete`` from a contributor is the
        plan's ``suggest_del`` over the same range — which is what
        ``as_plan_bodies`` already does for their ingest — unless the range
        holds nothing but words their own open plan put there, which is theirs
        to take back and not a proposal to strike a proposal. A ``segments``
        delete is an undo of something, and the policy judges it by the same
        rule."""
        if s.kind != K.TEXT_DELETE or s.plan or self.roles.role(actor) != SUGGEST:
            return s
        if "from" not in s.payload or "to" not in s.payload:
            return s
        if _own_plan_words(self.store, actor, deleted_ops(self.store, s.target, s.payload)):
            return s        # their own proposed words: a real delete, not a proposal to strike a proposal
        return L.Submission(cid=s.cid, kind=K.TEXT_MARK, target=dict(s.target),
                            payload={"kind": SUGGEST_DEL, "attrs": {}, "start": s.payload["from"],
                                     "end": s.payload["to"], "on": True},
                            batch=s.batch, plan=s.plan, base=s.base, caused_by=s.caused_by)

    def _plan_for(self, actor: str, batch: str, bid: str, note: str) -> str | None:
        """The plan this actor's writes ride in, opened on demand. One per edit,
        not one per batch: the editor flushes every 350 ms, so a contributor
        typing a sentence would otherwise leave the maintainer four proposals to
        answer. An open plan of theirs on the same block is that edit."""
        key = (actor, batch)
        pid = self._opened_plans.get(key)
        plan = self.store.plans.get(pid or "")
        if plan is not None and plan.status == "open":
            return pid
        if bid:
            # An `edit` plan only: a proposal to DELETE or MOVE this feature is
            # a different question, and filing a repair under it means accepting
            # the deletion accepts the repair and rejecting it drops it.
            for p in reversed(list(self.store.plans.values())):
                if p.status == "open" and p.actor == actor and p.target == bid and p.kind == "edit":
                    self._opened_plans[key] = p.id
                    return p.id
        pid = new_plan_id()
        opened = self._apply(self._mint(K.PLAN_OPEN, {"pid": pid},
                                        {"kind": "edit", "target": bid if bid in self.store.blocks else None,
                                         "note": note}, actor=actor, batch=pid))
        if opened.conflict:
            return None
        self._opened_plans[key] = pid
        return pid

    def _suggester_plan(self, actor: str, kind: str, target: dict, payload: dict, batch: str) -> str | None:
        """The plan a contributor's trunk edit rides in (02 §3.6: "a
        contributor's edit on the trunk is applied as a plan with that actor").
        The daemon opens it, because the browser has no way to know it needs
        one, and one per submitted batch, because a plan per keystroke would be
        a verdict per keystroke. A comment needs none; anything the calculus
        cannot carry under a plan is left untagged for the policy to refuse."""
        if self.roles.role(actor) != SUGGEST or kind not in K.PLANNABLE:
            return None
        if is_comment_op(self.store, kind, target, payload):
            return None
        return self._plan_for(actor, batch, str(target.get("bid") or target.get("into") or ""), "suggested from the hub")

    def _drain_one(self, client: str, actor: str, s: L.Submission, acked: dict[str, str]) -> list[Op]:
        batch = s.batch or new_batch_id()
        if s.caused_by and actor_kind(actor) not in MACHINE_KINDS:
            # `caused_by` says "this op is what that hand-off produced" and the
            # provenance card and `settle` read it. A person's client does not
            # get to assert it about itself.
            s = replace(s, caused_by=None)
        s = self._as_proposal(actor, s)
        plan = s.plan or self._suggester_plan(actor, s.kind if s.kind != "ingest" else K.TEXT_INSERT,
                                              s.target, s.payload, batch)
        if s.kind == "ingest":
            # A whole-text write from another process: the boundary diff runs
            # here, in the applier. The ack names the first op.
            res, ops = self.ingest(s.target["bid"], s.target.get("path", "text"), base_text=s.payload["base_text"],
                                   new_md=s.payload["new_md"], actor=actor, plan=plan, caused_by=s.caused_by,
                                   batch=s.batch or None)
            L.ack(self.codoc_dir, client, s.cid, ops[0] if ops else None, error=res.refused,
                  status=None if ops or res.refused else "unchanged")
            acked[s.cid] = ops[0].id if ops else ""
            return ops
        payload = _resolve_provisional(s.payload, acked, self._sent_text)
        op = self._apply(self._mint(s.kind, s.target, payload, actor=actor, batch=batch,
                                    plan=plan, base=s.base, caused_by=s.caused_by))
        if s.kind == K.TEXT_INSERT and isinstance(s.payload.get("text"), str):
            self._sent_text[s.cid] = s.payload["text"]
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


def _resolve_provisional(payload: dict, minted: dict[str, str], sent: dict[str, str] | None = None) -> dict:
    """Replace ``["$cid", cid, k]`` anchors with ``[op_id, k]`` once the insert
    with that client id has been minted. An unresolvable one is left as is and
    fails as an unknown anchor, which is the honest outcome.

    ``k`` is the client's own scalar index into the text it sent; the applier
    stores NFC, so a client that sent a decomposed ``e + ́`` counted one
    character more than the store has. ``sent`` lets the index be mapped
    instead of guessed, so a paste from a Mac anchors where the person meant."""
    out = dict(payload)
    for key in ("after", "from", "to", "start", "end", "at"):
        v = out.get(key)
        if isinstance(v, list) and len(v) == 3 and v[0] == "$cid" and minted.get(v[1]):
            out[key] = [minted[v[1]], _fold_index(v[2], (sent or {}).get(v[1]))]
    if isinstance(out.get("segments"), list):
        out["segments"] = [[minted.get(seg[1], seg[0]) if isinstance(seg[0], str) and seg[0] == "$cid" else seg[0], *seg[1 + (seg[0] == "$cid"):]]
                           if isinstance(seg, list) and seg and seg[0] == "$cid" else seg for seg in out["segments"]]
    return out


def _fold_index(k: int, sent: str | None) -> int:
    """The index of the client's k-th scalar in the NFC text the store kept."""
    if not sent or nfc(sent) == sent:
        return k
    scalars = list(sent)
    return max(0, len(nfc("".join(scalars[: k + 1]))) - 1)
