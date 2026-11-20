"""``Workspace``: a ``.codoc`` directory opened as the applier (docs/concurrency.md).

One process opens a workspace; it is the applier. Opening replays the logs
(snapshot first, then every op outside the snapshot's frontier, in
``(at, session, seq)`` order), advances the clock past everything replayed, and
starts a new session log. ``follow_disk`` keeps the store a function of the
logs on disk: when ``HEAD`` moved or git took the session file it starts a new
session and rebuilds, and when any other log file changed it rebuilds. ``submit`` mints ids, stamps actors, applies, and appends
to the session log; ``ingest`` runs the boundary diff and submits its ops as
one batch under the machine gate. ``drain_inbox`` turns client submissions into
ops and acks them by client id, each exactly once.

Batches are not atomic: an op that conflicts is recorded and the rest of the
batch still applies (the loop relies on this: a plan whose third paragraph
fails to ingest keeps its first two). A caller that needs all-or-nothing checks
its bodies before submitting.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field, replace
from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.apply import apply, replay_recorded
from codoc.calculus.derive import HELD, block_written_by, phase
from codoc.calculus.doc import is_one_line, is_source_field
from codoc.calculus.ids import MACHINE_KINDS, actor_kind, new_batch_id, new_plan_id, new_session_id, op_id
from codoc.calculus.ingest import Ingest, as_plan_bodies, ingest, resolve_pending_refs, strike_bodies
from codoc.calculus.ops import Op, frontier_union
from codoc.calculus.store import Store
from codoc.calculus.text import SUGGEST_DEL, AnchorError
from codoc.workspace import inbox as I
from codoc.workspace import log as L
from codoc.workspace.clock import Clock
from codoc.workspace.roles import SUGGEST, Roles, deleted_ops, is_comment_op, own_plan_words

# The fraction of a block a machine may delete on the trunk before its write
# becomes a plan; a person's prose is held tighter (docs/concurrency.md, The
# machine gate).
GATE_MACHINE = 0.5
GATE_HUMAN = 0.15


class Workspace:
    def __init__(self, codoc_dir: str | Path, *, roles: Roles | None = None,
                 session: str | None = None, readonly: bool = False) -> None:
        self.codoc_dir = Path(codoc_dir)
        self.session = session or new_session_id()
        self.roles = roles or Roles.load(self.codoc_dir)
        # The clock's node is the session id: unique per writer, and it names
        # no machine in a tracked log.
        self.clock = Clock(self.session)
        self.store = Store(policy=self.roles.policy)
        self.problems: list[L.LogProblem] = []
        self.torn: list[L.SessionFile] = []
        # (actor, batch) → the plan this workspace opened for it: a
        # contributor's trunk edit AND a machine write the gate demoted.
        self._opened_plans: dict[tuple[str, str], str] = {}
        self._inbox_offsets: dict[str, int] = I.read_offsets(self.codoc_dir)
        if not readonly:
            I.rotate_acks(self.codoc_dir)
        self._acked: dict[str, dict[str, str]] = I.acked(self.codoc_dir)   # client → cid → op id
        self._logged: dict[str, dict[str, str]] = {}  # client → cid → first op id, as the logs record it
        self._answering: tuple[str, str] | None = None  # (client, cid) of the submission being applied
        self._sent_text: dict[str, str] = {}      # cid → the insert's text as the client counted it
        # The HEAD (sha, branch) this session writes under, as the caller read
        # it; None until a caller says (``follow_disk``).
        self.head: tuple[str, str] | None = None
        self._loaded: dict[str, tuple[int, int]] = {}   # the other log files the store was built from
        self.rebuild()
        self.readonly = readonly
        self.seq = 0
        # A read-only open (an MCP read, `codoc status`) replays the logs and
        # writes nothing: no session file, and `submit` refuses.
        self.writer = None if readonly else L.SessionWriter(self.codoc_dir, self.session, self.clock.tick())
        if not readonly:
            self._answer_logged()

    # ── replay ─────────────────────────────────────────────────────────────
    def rebuild(self) -> None:
        """Fold every valid log on disk into a fresh store (I6). The log is the
        truth: nothing replayed is refused, and the clock advances past it."""
        # Taken before the files are read, so a write landing during the read
        # is seen as a change at the next look.
        self._loaded = self._other_logs()
        self.store = Store(policy=self.roles.policy)
        self.problems = []
        self.torn = []
        self._logged = {}
        files = []
        for p in L.list_sessions(self.codoc_dir):
            f = L.read_session(p)
            self.problems.extend(f.problems)
            if f.torn_tail is not None:
                self.torn.append(f)
            if f.valid:
                files.append(f)
                for (client, cid), oid in f.subs.items():
                    self._logged.setdefault(client, {}).setdefault(cid, oid)
        exclude = None
        snaps = [L.read_session(p) for p in L.list_snapshots(self.codoc_dir)]
        for s in snaps:
            self.problems.extend(s.problems)
        valid = [s for s in snaps if s.valid]
        # With two folds (two clones each folded, and a merge brought both), the
        # one whose frontier covers more ops is the state, a tie going to the
        # later name, so every clone that merges the same files picks the same.
        snap = max(valid, key=lambda s: (sum((s.header.get("frontier") or {}).values()), s.path.name), default=None)
        if len(snaps) > 1:
            # The other fold's log is in `log/archive/`, which replay does not
            # read; read it here, past the chosen frontier, rather than lose a
            # feature with no message. Fold once, on one branch.
            others = ", ".join(s.path.name for s in snaps if s is not snap)
            self.problems.append(L.LogProblem(str(snap.path if snap else snaps[-1].path), 1, (
                f"{len(snaps)} snapshots: {others} was folded by another clone; "
                "replaying its archived log. Re-fold once, on one branch.")))
            for p in L.list_archived(self.codoc_dir):
                f = L.read_session(p)
                if f.valid and not f.header.get("snapshot"):
                    files.append(f)
        if snap is not None:
            exclude = snap.header.get("frontier") or {}
            for op in snap.ops:
                self._replay_one(op)
            # The folded ops ARE in this state, so the frontier has to say
            # so. A frontier is a vector clock and a fold drops the folded
            # sessions' entries from it; without this, everything a later
            # watermark should cover reads as uncovered: a hand-off given
            # after a fold would not carry the words the person removed
            # before it, and `covered()` would call settled work in flight.
            self.store.frontier = frontier_union(self.store.frontier, exclude)
        for op in L.replay_order(files, exclude):
            self._replay_one(op)

    def _replay_one(self, op: Op) -> None:
        """Replay one recorded op, advancing the clock past it.
        ``apply.replay_recorded`` is the rule, and says why.

        An op whose record says it applied and which conflicts here is the one
        thing replay cannot make true again: two clones forked, one accepted
        the plan the other went on writing into, and the merge has to choose.
        It chooses the earlier op, which is right, and says so, because the
        loser's own log says those words landed and their tree will not have
        them. `codoc rebuild` and the daemon both print `problems`."""
        self.clock.observe(op.at)
        got = replay_recorded(self.store, op)
        if op.conflict is None and got.conflict is not None:
            self.problems.append(L.LogProblem(
                op.id, 0, f"{op.kind} by {op.actor} applied when it was written and "
                          f"conflicts on this merge ({got.conflict}); those words are not in this tree"))

    # ── writing ────────────────────────────────────────────────────────────
    def _mint(self, kind: str, target: dict, payload: dict, *, actor: str, batch: str,
              plan: str | None = None, base: dict | None = None, caused_by: str | None = None) -> Op:
        self.seq += 1
        return Op(id=op_id(self.session, self.seq), at=self.clock.tick(), actor=actor, batch=batch,
                  kind=kind, target=dict(target), payload=dict(payload), base=dict(base or {}),
                  plan=plan, caused_by=caused_by)

    def _apply(self, op: Op, *, answers: bool = True) -> Op:
        """Apply and log one op. While a client submission is being applied
        its ops are logged naming it, except those the applier writes on its
        own account (``answers=False``): opening the plan a write rides in,
        and taking back a plan nothing landed under."""
        if self.writer is None:
            raise RuntimeError("read-only workspace: submit through daemon.client")
        apply(self.store, op)
        self.writer.append(op, self._answering if answers else None)
        return op

    def submit(self, bodies: list[dict], *, actor: str, batch: str | None = None, plan: str | None = None,
               base: dict | None = None, caused_by: str | None = None) -> list[Op]:
        """Mint, apply and log a batch of op bodies ``{kind, target, payload}``
        (see the module note on atomicity). An anchor ``["$ins", i, k]`` names
        the k-th character of the i-th ``text.insert`` in ``bodies``."""
        batch = batch or new_batch_id()
        out: list[Op] = []
        insert_ids: list[str] = []
        for b in bodies:
            payload = dict(b.get("payload", {}))
            resolve_pending_refs(payload, insert_ids)
            op = self._apply(self._mint(b["kind"], b.get("target", {}), payload, actor=actor, batch=batch,
                                        plan=b.get("plan", plan), base=base, caused_by=b.get("caused_by", caused_by)))
            if b["kind"] == K.TEXT_INSERT:
                insert_ids.append(op.id)
            out.append(op)
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
                    trunk: bool = True, plan: str | None = None) -> Ingest:
        """The boundary diff, computed and not applied: what ``new_md`` would do
        to the field. ``trunk`` says the ops will land on the trunk, where a
        machine may not drop a person's mark; under a plan the person decides.

        A field holding words that an open plan other than ``plan`` inserted
        is refused (``open_plan``). A writer is shown the text without them
        (the ``rejected`` read mode), so its diff would delete them. A plan's
        marks leave the shown text as it is and do not count."""
        block = self.store.blocks.get(bid)
        if block is None:
            return Ingest(refused="no_block")
        if not block.live:
            return Ingest(refused="dead_block")
        if path not in block.layout.text:
            return Ingest(refused="not_text")
        text = block.text(path)
        if any(s.live and s.plan != plan and s.plan in self.store.open_plans for s in text.segs):
            return Ingest(refused="open_plan")
        return ingest(text, bid=bid, path=path, base_text=base_text, new_md=new_md, actor=actor,
                      trunk=trunk, multiline=not is_one_line(block.kind), raw=is_source_field(block.kind, path))

    def commit_ingest(self, res: Ingest, *, actor: str, plan: str | None = None, batch: str | None = None,
                      base: dict | None = None, caused_by: str | None = None) -> list[Op]:
        """Mint and apply a computed ingest as one batch, under ``plan`` if
        given (its deletes become ``suggest_del`` marks). Every op records the
        frontier the diff ran at as ``base``."""
        if res.refused:
            return []
        bodies = as_plan_bodies(res.bodies, plan) if plan else res.bodies
        return self.submit(bodies, actor=actor, plan=plan, batch=batch or plan,
                           base=base if base is not None else dict(self.store.frontier), caused_by=caused_by)

    def ingest(self, bid: str, path: str, *, base_text: str, new_md: str, actor: str, base: dict | None = None,
               plan: str | None = None, caused_by: str | None = None, batch: str | None = None,
               derived: bool = False) -> tuple[Ingest, list[Op]]:
        """``plan_ingest`` then ``commit_ingest``, on the trunk or under ``plan``.

        A machine writing one field on its own is gated here
        (``_must_propose``, docs/concurrency.md, The machine gate): a write
        the trunk refuses because it would drop a mark a person placed, one
        that deletes more of the block than its gate allows, or any write to a
        feature with pending intent becomes a plan of that actor's. The
        document wins and the person decides. A repair of several blocks goes
        through ``amend``, which asks the same gate per block and opens one
        plan for all of it.

        ``derived`` says this content was READ OFF THE CODE rather than
        authored: the lift pass's re-derived diagram, and nothing else. The
        gate exists to keep a machine from overwriting words somebody meant,
        and a derived field holds none: it is attribution, like a binding
        refresh. Gating it would turn every large code change into a plan
        to redraw the diagram, which is when the diagram most needs to follow
        the code."""
        res = self.plan_ingest(bid, path, base_text=base_text, new_md=new_md, actor=actor, trunk=plan is None,
                               plan=plan)
        opened = None
        if plan is None and not derived and (why := self._must_propose(bid, actor, res)):
            plan = opened = self._plan_for(actor, batch or bid, bid, why)
            if plan is not None:
                batch = batch or plan
                res = self.plan_ingest(bid, path, base_text=base_text, new_md=new_md, actor=actor, trunk=False,
                                       plan=plan)
        if res.refused:
            self._withdraw_if_empty(opened)
            return res, []
        ops = self.commit_ingest(res, actor=actor, plan=plan, batch=batch, base=base, caused_by=caused_by)
        if not ops:
            self._withdraw_if_empty(opened)
        return res, ops

    def amend(self, fid: str, *, rewrites: list[tuple], bodies: list[dict] = (), strikes: list[str] = (),
              actor: str, note: str = "", caused_by: str | None = None, batch: str | None = None,
              structural: bool = False, whole: bool = False, join: str | None = None) -> "Amended":
        """A machine's repair of one feature, decided once for all of it.

        ``rewrites`` are ``(bid, path, new_md, base_text | None)``: new text
        for existing fields (``base_text`` is what the writer read; None means
        the current text). ``bodies`` add or re-kind blocks; ``strikes`` are
        blocks proposed for removal. The whole amend lands on the trunk only
        when it is all rewording, the feature has no pending intent, and every
        rewording passes ``_must_propose``; otherwise it is one plan, so one
        repair gets one verdict. A rewrite ``plan_ingest`` refuses is left
        out and reported; with ``whole``, nothing lands when one is refused.
        ``join`` is an open plan of the actor's on this feature: the plan, when
        one is needed, is that one, and a field holding its words is rewritten
        under it.

        Everything the amend writes is one batch, so it reads as one change: a
        plan it opens is its batch, otherwise ``batch`` (the caller's, such as
        a reflect pass's) or a new one. A joined plan's new words are a change
        of their own, written later than the plan's first, so they are not
        filed under the plan's batch."""
        out = Amended()
        store = self.store
        held = fid in store.blocks and phase(store, fid) in HELD
        need_plan = held or structural or bool(strikes)
        base = dict(store.frontier)
        computed: list[Ingest] = []
        for bid, path, new_md, base_text in rewrites:
            block = store.blocks.get(bid)
            if base_text is None:
                base_text = block.text(path).live_text() if block is not None and path in block.layout.text else ""
            ing = self.plan_ingest(bid, path, base_text=base_text, new_md=new_md, actor=actor, trunk=not need_plan)
            if join and ing.refused == "open_plan":
                need_plan = True
                ing = self.plan_ingest(bid, path, base_text=base_text, new_md=new_md, actor=actor, trunk=False, plan=join)
            if self._must_propose(bid, actor, ing):
                need_plan = True
                if ing.refused:
                    ing = self.plan_ingest(bid, path, base_text=base_text, new_md=new_md, actor=actor, trunk=False)
            if ing.refused:
                out.refused.append((bid, ing.refused))
            elif not ing.unchanged:
                computed.append(ing)
        if not (computed or bodies or strikes) or (whole and out.refused):
            return out
        if need_plan and join:
            out.plan = join
        elif need_plan:
            out.plan = batch = self.open_plan(actor=actor, kind="edit", target=fid,
                                              note=note or ("the document has pending intent" if held else "a larger repair"))
            out.ops.append(store.by_id[store.plans[out.plan].opened_by])
        batch = batch or new_batch_id()
        for ing in computed:
            out.ops += self.commit_ingest(ing, actor=actor, plan=out.plan, batch=batch, base=base,
                                          caused_by=caused_by)
        tail = list(bodies) + (strike_bodies(store, list(strikes), out.plan) if out.plan else [])
        if tail:
            out.ops += self.submit(tail, actor=actor, plan=out.plan, batch=batch, caused_by=caused_by)
        return out

    def propose(self, *, actor: str, kind: str, target: str | None, note: str, build: bool = False,
                bodies: list[dict] = (), rewrites: list[tuple] = (), strikes: list[str] = (),
                caused_by: str | None = None, pid: str | None = None) -> "Amended":
        """A machine's plan, decided whole: one plan holding ``bodies``,
        the ``rewrites`` ``(bid, path, new_md, base_text | None)`` and the
        ``strikes``. When nothing lands under it the plan is taken back, so the
        person is never asked to accept nothing; a delete plan stays, since
        accepting it is the deletion. ``out.plan`` is None when no plan stands."""
        out = Amended()
        pid = pid or new_plan_id()
        # What can fail on a malformed submission fails here, before anything
        # lands: a plan is decided whole, and a plan half written would stay.
        if any(not isinstance(md, str) or not (base is None or isinstance(base, str)) for _, _, md, base in rewrites):
            raise TypeError("a rewrite's new_md and base_text are strings")
        struck = strike_bodies(self.store, list(strikes), pid) if strikes else []
        opened = self.submit([{"kind": K.PLAN_OPEN, "target": {"pid": pid}, "payload": {
            "kind": kind, "target": target, "note": note, **({"build": True} if build else {})}}],
            actor=actor, batch=pid, caused_by=caused_by)[0]
        if opened.conflict:
            out.refused.append(("", f"plan.open: {opened.conflict}"))
            return out
        out.plan, landed = pid, []
        try:
            if bodies:
                landed += self.submit(list(bodies), actor=actor, plan=pid, batch=pid, caused_by=caused_by)
            for bid, path, new_md, base_text in rewrites:
                block = self.store.blocks.get(bid)
                if base_text is None:
                    base_text = block.text(path).live_text() if block is not None and path in block.layout.text else ""
                res, ops = self.ingest(bid, path, base_text=base_text, new_md=new_md, actor=actor, plan=pid,
                                       batch=pid, caused_by=caused_by)
                if res.refused:
                    out.refused.append((bid, res.refused))
                landed += ops
            if struck:
                landed += self.submit(struck, actor=actor, plan=pid, batch=pid, caused_by=caused_by)
        finally:
            # Whatever stopped it, a plan nothing landed under is taken back;
            # a delete plan stays, since accepting it is the deletion.
            if kind != "delete":
                self._withdraw_if_empty(pid)
        out.refused += [(o.target.get("bid", ""), o.conflict) for o in landed if o.conflict]
        if pid not in self.store.open_plans:
            out.plan = None
        else:
            out.ops = [opened] + [o for o in landed if not o.conflict]
        return out

    def _must_propose(self, bid: str, actor: str, res: Ingest) -> str | None:
        """Why this machine write may not land on the trunk, or None: the
        machine gate, asked by ``ingest`` for one field and by ``amend`` for
        each field of a repair. A translation replaces every word, so whose
        words they were decides it, not the fraction deleted."""
        if actor_kind(actor) not in MACHINE_KINDS:
            return None
        if res.refused:
            return "it would drop a mark the person placed" if res.refused.startswith("would_drop_human_") else None
        if res.unchanged:
            return None
        fid = self.store.feature_of(bid)
        if fid and phase(self.store, fid) in HELD:
            return "the document has pending intent"
        human = block_written_by(self.store, bid) == "human"
        if actor_kind(actor) == "translator":
            return "a translation of a person's words" if human else None
        return "a larger repair" if res.deleted_fraction > (GATE_HUMAN if human else GATE_MACHINE) else None

    # ── clients ────────────────────────────────────────────────────────────
    def drain_inbox(self, identity=I.actor_for) -> list[Op]:
        """Apply every new submission from every client, each exactly once.
        ``identity`` maps a client name and this workspace's directory to the
        actor the applier stamps (a callable), or a client name to it (a
        dict); a client with no actor is not read (a file nobody vouches for
        writes nothing).

        Submissions apply in the order they were appended: each line carries
        the wall-clock time of its append (``at``), every client of a checkout
        appends on this machine, and the clients' lines are merged by it, each
        file keeping its own order. Only whole lines are consumed; a malformed
        one is acked with an error so the client learns. A submission the logs
        already record (the applier stopped between logging its ops and
        writing its ack) is acked from the log and not applied again.

        A client cannot know the op ids of characters it typed a moment ago and
        has not yet seen acked, so an anchor may name them provisionally as
        ``["$cid", "<client id>", k]``: the k-th character of the insert that
        submission minted. The drain resolves those against every ack it has
        given that client, so a chain of typing survives a drain boundary the
        client did not choose. No line can block the next: a submission that
        raises is acked with the error and left behind."""
        queues = []
        offsets = dict(self._inbox_offsets)
        for client in I.clients(self.codoc_dir):
            actor = identity(client, self.codoc_dir) if callable(identity) else identity.get(client)
            if actor is None:
                continue
            lines = I.read_inbox(self.codoc_dir, client, offsets.get(client, 0))
            queues.append([(line.sub.at if line.sub else 0.0, client, actor, line) for line in lines])
            if lines:
                offsets[client] = lines[-1].end
        todo: list[tuple[str, str, I.Submission]] = []
        taken: set[tuple[str, str]] = set()
        for _, client, actor, line in heapq.merge(*queues, key=lambda q: q[0]):
            if line.sub is None:
                I.ack(self.codoc_dir, client, line.cid, error=line.error)
                continue
            cid = line.sub.cid
            if (client, cid) in taken or self._answered(client, cid):
                continue
            taken.add((client, cid))
            todo.append((client, actor, line.sub))
        out: list[Op] = []
        for got in self.apply_submissions(todo, on_applied=lambda client, s, got: I.ack(
                self.codoc_dir, client, s.cid, got.ops[0] if got.ops else None, error=got.conflict,
                status=got.status)):
            out.extend(got.ops)
        if offsets != self._inbox_offsets:
            self._inbox_offsets = offsets
            I.write_offsets(self.codoc_dir, offsets)
        return out

    def _answered(self, client: str, cid: str) -> bool:
        """Whether submission ``cid`` of ``client`` has its answer: it was
        acked, or the logs record its ops (the applier stopped between logging
        them and writing the ack), in which case it is acked now from the log.
        Either way it is not applied again."""
        acked = self._acked.setdefault(client, {})
        if cid in acked:
            return True
        logged = self._logged.get(client, {}).get(cid)
        if logged is None:
            return False
        I.ack(self.codoc_dir, client, cid, self.store.by_id.get(logged))
        acked[cid] = logged
        return True

    def _answer_logged(self) -> None:
        """Answer every undrained submission the logs already record, when a
        writing workspace opens: before a fold archives the file that records
        it, and before a drain could apply it twice."""
        for client in self._logged:
            for line in I.read_inbox(self.codoc_dir, client, self._inbox_offsets.get(client, 0)):
                if line.sub is not None:
                    self._answered(client, line.sub.cid)

    def apply_submissions(self, subs: list[tuple[str, str, I.Submission]], on_applied=None) -> list["Applied"]:
        """Apply ``(client, actor, submission)`` triples in order
        (``apply_submission``), then take back every plan this workspace
        opened for them that nothing landed under: the person would be asked
        for a verdict on nothing. A plan the client opened itself (a
        ``plan.open`` submission) is its own, and its ops may come in a later
        call. The daemon's drain and a daemonless writer (``daemon.client``)
        both come through here. ``on_applied(client, sub, applied)`` runs
        after each one, before the next: the drain acks there."""
        before = set(self.store.plans)
        out = []
        for client, actor, s in subs:
            out.append(got := self.apply_submission(client, actor, s))
            if on_applied is not None:
                on_applied(client, s, got)
        for pid in (set(self.store.plans) - before) & set(self._opened_plans.values()):
            self._withdraw_if_empty(pid)
        self._sync()
        return out

    def _withdraw_if_empty(self, pid: str | None) -> None:
        """Take back a plan this workspace opened that nothing landed under: the
        editor would offer a verdict on nothing."""
        plan = self.store.open_plans.get(pid or "")
        if plan is None or plan.ops:
            return
        self._apply(self._mint(K.PLAN_REJECT, {"pid": plan.id}, {}, actor=plan.actor, batch=plan.id), answers=False)
        self._opened_plans = {k: v for k, v in self._opened_plans.items() if v != plan.id}

    def _as_strike(self, actor: str, s: I.Submission) -> I.Submission:
        """A suggester's Backspace, as a strike in their plan. A plan never
        kills, so a contributor's ``text.delete`` becomes the plan's
        ``suggest_del`` over the characters it names, unless they are all
        words the contributor's own open plan put there, which are theirs to
        take back. A ``segments`` delete names its characters exactly, and the
        mark records exactly those (``chars``), so a word another actor typed
        between them is not struck. A delete inside a comment is not one: the
        comment is written on the trunk, and the policy decides whose words
        it may take."""
        if s.kind != K.TEXT_DELETE or s.plan or self.roles.role(actor) != SUGGEST \
                or is_comment_op(self.store, s.kind, s.target, s.payload):
            return s
        if own_plan_words(self.store, actor, deleted_ops(self.store, s.target, s.payload)):
            return s        # their own plan's words: a real delete, not a strike of a strike
        if "segments" in s.payload:
            payload = self._strike_segments(s.target, s.payload["segments"])
        elif "from" in s.payload and "to" in s.payload:
            payload = {"kind": SUGGEST_DEL, "attrs": {}, "start": s.payload["from"], "end": s.payload["to"], "on": True}
        else:
            return s
        if payload is None:
            return s        # names nothing live here: the applier reports why
        return I.Submission(cid=s.cid, kind=K.TEXT_MARK, target=dict(s.target), payload=payload,
                            batch=s.batch, plan=s.plan, base=s.base, caused_by=s.caused_by)

    def _strike_segments(self, target: dict, segments) -> dict | None:
        """A ``suggest_del`` payload over exactly the live characters ``segments``
        names: from the first to the last in sequence order, with ``chars``
        listing them, so accepting kills those and nothing between them."""
        try:
            chars = [(str(op), int(k0) + i) for op, k0, n in segments for i in range(int(n))]
            block, path = self.store.locate_char(chars[0], (str(target.get("bid") or ""), str(target.get("path") or "text")))
        except (AnchorError, IndexError, TypeError, ValueError):
            return None
        text = block.text(path)
        live = [c for c in chars if text.holds(c) and text.segs[text.locate(c)[0]].live]
        if not live:
            return None
        live.sort(key=text.locate)
        return {"kind": SUGGEST_DEL, "attrs": {"chars": ",".join(f"{op}:{k}" for op, k in live)},
                "start": list(live[0]), "end": list(live[-1]), "on": True}

    def _plan_for(self, actor: str, batch: str, bid: str, note: str) -> str | None:
        """The plan this actor's writes ride in, opened on demand. One per edit,
        not one per batch: the editor flushes every 350 ms, so a contributor
        typing a sentence would otherwise leave the maintainer four plans to
        answer. An open plan of theirs on the same block is that edit."""
        key = (actor, batch)
        pid = self._opened_plans.get(key)
        plan = self.store.plans.get(pid or "")
        if plan is not None and plan.status == "open":
            return pid
        if bid:
            # An `edit` plan only: a plan to delete or move this feature is
            # a different question, and filing a repair under it means accepting
            # the deletion accepts the repair and rejecting it drops it.
            for p in reversed(list(self.store.open_plans.values())):
                if p.actor == actor and p.target == bid and p.kind == "edit":
                    self._opened_plans[key] = p.id
                    return p.id
        pid = new_plan_id()
        opened = self._apply(self._mint(K.PLAN_OPEN, {"pid": pid},
                                        {"kind": "edit", "target": bid if bid in self.store.blocks else None,
                                         "note": note}, actor=actor, batch=pid), answers=False)
        if opened.conflict:
            return None
        self._opened_plans[key] = pid
        return pid

    def _suggester_plan(self, actor: str, kind: str, target: dict, payload: dict, batch: str) -> str | None:
        """The plan a contributor's trunk edit rides in: a suggester's
        untagged edit is applied as a plan of that actor. The applier opens it,
        because the browser has no way to know it needs one, and one per
        submitted batch, because a plan per keystroke would be a verdict per
        keystroke. A comment needs none; anything the calculus
        cannot carry under a plan is left untagged for the policy to refuse."""
        if self.roles.role(actor) != SUGGEST or kind not in K.PLANNABLE:
            return None
        if is_comment_op(self.store, kind, target, payload):
            return None
        return self._plan_for(actor, batch, str(target.get("bid") or target.get("into") or ""), "suggested from the hub")

    def apply_submission(self, client: str, actor: str, s: I.Submission) -> "Applied":
        """Apply one client submission as ``actor``: what a client may assert,
        how a suggester's edit becomes a plan, and what an amend or an ingest
        answers are decided here and nowhere else. Total: a submission that
        raises is refused with the error. Its ops are logged naming it,
        ``sub: [client, cid]`` (``_apply``), and the first is remembered under
        the client id, so a later ``["$cid", cid, k]`` anchor resolves."""
        acked = self._acked.setdefault(client, {})
        self._answering = (client, s.cid)
        try:
            got = self._apply_one(actor, s, acked)
        except Exception as e:  # noqa: BLE001 (the applier is total; so is the drain)
            got = Applied(conflict=f"refused: {e!r}")
        finally:
            self._answering = None
        if got.conflict is None and got.ops:
            got.conflict = got.ops[0].conflict
        acked[s.cid] = got.ops[0].id if got.ops else ""
        return got

    def _apply_one(self, actor: str, s: I.Submission, acked: dict[str, str]) -> "Applied":
        first = self.store.batch_first.get(s.batch) if s.batch else None
        if first is not None and self.store.ops[first].actor != actor:
            # Another actor's batch: an undo of it sends the inverse of every op
            # in it as the undoer, so a batch id read from history.json must not
            # put this actor's op there.
            s = replace(s, batch="")
        batch = s.batch or new_batch_id()
        if s.caused_by and actor_kind(actor) not in MACHINE_KINDS:
            # `caused_by` says "this op is what that hand-off produced" and the
            # provenance card and `settle` read it. A person's client does not
            # get to assert it about itself.
            s = replace(s, caused_by=None)
        s = replace(s, payload=I.client_payload(self.store, s.kind, s.payload, actor, s.target))
        if s.kind == "amend":
            # The amend decides its own plan, so no suggester plan is opened for it.
            p = s.payload
            done = self.amend(str(s.target.get("fid") or ""), actor=actor, note=str(p.get("note") or ""), caused_by=s.caused_by,
                              rewrites=[(r["bid"], r.get("path", "text"), r["new_md"], r.get("base_text"))
                                        for r in p.get("rewrites") or []],
                              bodies=p.get("bodies") or [], strikes=p.get("strikes") or [],
                              structural=bool(p.get("structural")))
            return Applied(done.ops, "; ".join(f"{b}: {why}" for b, why in done.refused) or None,
                           (f"plan:{done.plan}" if done.plan else "trunk") if done.ops else "unchanged")
        if s.kind == "propose":
            p = s.payload
            done = self.propose(actor=actor, kind=str(p.get("kind") or "edit"), target=p.get("target"), pid=s.target.get("pid"),
                                note=str(p.get("note") or ""), build=bool(p.get("build")), caused_by=s.caused_by,
                                bodies=p.get("bodies") or [], strikes=p.get("strikes") or [],
                                rewrites=[(r["bid"], r.get("path", "text"), r["new_md"], r.get("base_text"))
                                          for r in p.get("rewrites") or []])
            return Applied(done.ops, "; ".join(f"{b}: {why}" if b else why for b, why in done.refused) or None,
                           f"plan:{done.plan}" if done.plan else "unchanged")
        if s.kind != "ingest":
            # Resolve first: `_as_strike` asks which plan minted the
            # characters a range names, and a `$cid` ref answers nothing. The
            # editor batches a flush, so an insert and the Backspace over it
            # arrive together exactly when somebody corrects a typo.
            s = replace(s, payload=I.resolve_provisional(s.payload, acked, self._sent_text))
        s = self._as_strike(actor, s)
        plan = s.plan or self._suggester_plan(actor, s.kind if s.kind != "ingest" else K.TEXT_INSERT,
                                              s.target, s.payload, batch)
        if s.kind == "ingest":
            # A whole-text write from another process: the boundary diff runs
            # here, in the applier. The ack names the first op.
            res, ops = self.ingest(s.target["bid"], s.target.get("path", "text"), base_text=s.payload["base_text"],
                                   new_md=s.payload["new_md"], actor=actor, plan=plan, caused_by=s.caused_by,
                                   batch=s.batch or None)
            return Applied(ops, res.refused, None if ops or res.refused else "unchanged")
        op = self._apply(self._mint(s.kind, s.target, s.payload, actor=actor, batch=batch,
                                    plan=plan, base=s.base, caused_by=s.caused_by))
        if s.kind == K.TEXT_INSERT and isinstance(s.payload.get("text"), str):
            self._sent_text[s.cid] = s.payload["text"]
        return Applied([op])

    # ── sessions ───────────────────────────────────────────────────────────
    def _sync(self) -> None:
        if self.writer is not None:
            self.writer.sync()

    def follow_disk(self, sha: str, branch: str) -> bool:
        """Before applying anything, make the store the one the logs on disk
        describe (I6). When ``HEAD`` moved since the last call (a commit, a
        checkout, a pull: its sha or its branch) or the session file is not the
        one the writer appends to (``git stash -u``, ``git clean``, a discard),
        start a new session and rebuild: a session file is never appended to
        after a commit may have recorded it. Otherwise rebuild when another log
        file changed (``catch_up``). The first call records ``HEAD``. Returns
        whether it rebuilt."""
        moved = self.head is not None and self.head != (sha, branch)
        self.head = (sha, branch)
        if moved or (self.writer is not None and not self.writer.intact()):
            self.rotate_session()
            self.rebuild()
            return True
        return self.catch_up()

    def catch_up(self) -> bool:
        """Rebuild when a log file other than this session's appeared, vanished
        or changed since the store was built: another writer applied, or git
        brought or took a file without moving ``HEAD`` (``git stash pop``).
        Returns whether it rebuilt."""
        if self._other_logs() == self._loaded:
            return False
        self.rebuild()
        return True

    def _other_logs(self) -> dict[str, tuple[int, int]]:
        """Every log file but this session's own, by size and modification time."""
        mine = f"{self.session}.jsonl"
        out = {}
        for p in L.log_dir(self.codoc_dir).glob("*.jsonl"):
            try:
                st = p.stat()
            except OSError:
                continue                # removed between the listing and the stat
            if p.name != mine:
                out[p.name] = (st.st_size, st.st_mtime_ns)
        return out

    def rotate_session(self) -> str:
        """Close the current session log and open a new one."""
        if self.writer is None:
            return self.session
        self.writer.close()
        self.session = self.clock.node = new_session_id()
        self.seq = 0
        self.writer = L.SessionWriter(self.codoc_dir, self.session, self.clock.tick())
        return self.session

    def close(self) -> None:
        if self.writer is not None:
            self.writer.close()


@dataclass
class Amended:
    plan: str | None = None                                       # the plan it opened, when it did
    ops: list[Op] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)  # (bid, why)


@dataclass
class Applied:
    """What one client submission became: the ops it minted, why it was
    refused in whole or in part (or the first op's conflict), and an outcome
    that is not a refusal (``plan:<id>``, ``trunk``, ``unchanged``). The ack
    carries all three."""
    ops: list[Op] = field(default_factory=list)
    conflict: str | None = None
    status: str | None = None

