"""``Workspace``: a ``.codoc`` directory opened as the applier (docs/concurrency.md).

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
from codoc.calculus.doc import is_one_line, is_source_field, is_text_field
from codoc.calculus.ids import MACHINE_KINDS, actor_kind, new_batch_id, new_plan_id, new_session_id, op_id
from codoc.calculus.ingest import Ingest, as_plan_bodies, ingest, resolve_pending_refs, strike_bodies
from codoc.calculus.ops import Op, frontier_union
from codoc.calculus.store import Store
from codoc.calculus.text import SUGGEST_DEL, AnchorError, nfc
from codoc.workspace import log as L
from codoc.workspace.clock import Clock
from codoc.workspace.roles import SUGGEST, Roles, deleted_ops, is_comment_op, own_plan_words


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
            # nothing reads. So report it and read the archive, rather than
            # lose a feature with no message. Fold once, on one branch.
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
                # watermark should cover reads as uncovered: a hand-off given
                # after a fold would not carry the words the person removed
                # before it, and `covered()` would call settled work in flight.
                self.store.frontier = frontier_union(self.store.frontier, exclude)
        for op in L.replay_order(files, exclude):
            self._replay_one(op)

    def _replay_one(self, op: Op) -> None:
        """Replay one recorded op, advancing the clock past it.
        ``apply.replay_recorded`` is the rule, and says why.

        An op whose record says it applied and which conflicts HERE is the one
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

    def _apply(self, op: Op) -> Op:
        if self.writer is None:
            raise RuntimeError("read-only workspace: submit through the daemon's inbox")
        apply(self.store, op)
        self.writer.append(op)
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
        if not is_text_field(block.kind, path):
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

        A machine writing prose on its own is gated HERE, because this is where
        every machine write passes: the loop's own pass, a translator, and the
        agent through ``codoc_reflect`` (docs/concurrency.md, The machine
        gate). A repair the trunk refuses because it would drop a mark the person placed, one that deletes
        more of the block than its gate allows, or any repair at all to a
        feature with pending intent, becomes a plan of that actor's. The
        document wins and the person decides. A caller that sees the whole
        repair and wants one plan for all of it (``reflect._amend``) computes
        and commits its parts itself, so it passes through here only once.

        ``derived`` says this content was READ OFF THE CODE rather than
        authored: the lift pass's re-derived diagram, and nothing else. The
        gate exists to keep a machine from overwriting words somebody meant,
        and a derived field holds none: it is attribution, like a binding
        refresh. Gating it would turn every large code change into a proposal
        to redraw the diagram, which is when the diagram most needs to follow
        the code."""
        res = self.plan_ingest(bid, path, base_text=base_text, new_md=new_md, actor=actor, trunk=plan is None,
                               plan=plan)
        opened = None
        if plan is None and not derived and (why := self.must_propose(bid, actor, res)):
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
              actor: str, note: str = "", caused_by: str | None = None, structural: bool = False,
              whole: bool = False) -> "Amended":
        """A machine's repair of one feature, decided once for all of it.

        ``rewrites`` are ``(bid, path, new_md, base_text | None)``: new text
        for existing fields (``base_text`` is what the writer read; None means
        the current text). ``bodies`` add or re-kind blocks; ``strikes`` are
        blocks proposed for removal. The whole amend lands on the trunk only
        when it is all rewording, the feature has no pending intent, and every
        rewording passes ``must_propose``; otherwise it is ONE plan, so one
        repair gets one verdict. A rewrite ``plan_ingest`` refuses is left
        out and reported; with ``whole``, nothing lands when one is refused."""
        out = Amended()
        store = self.store
        held = fid in store.blocks and phase(store, fid) in HELD
        need_plan = held or structural or bool(strikes)
        base = dict(store.frontier)
        computed: list[Ingest] = []
        for bid, path, new_md, base_text in rewrites:
            block = store.blocks.get(bid)
            if base_text is None:
                base_text = block.text(path).live_text() if block is not None and is_text_field(block.kind, path) else ""
            ing = self.plan_ingest(bid, path, base_text=base_text, new_md=new_md, actor=actor, trunk=not need_plan)
            if self.must_propose(bid, actor, ing):
                need_plan = True
                if ing.refused:
                    ing = self.plan_ingest(bid, path, base_text=base_text, new_md=new_md, actor=actor, trunk=False)
            if ing.refused:
                out.refused.append((bid, ing.refused))
            elif not ing.unchanged:
                computed.append(ing)
        if not (computed or bodies or strikes) or (whole and out.refused):
            return out
        if need_plan:
            out.plan = self.open_plan(actor=actor, kind="edit", target=fid,
                                      note="the document has pending intent" if held else note or "a larger repair")
            out.ops.append(store.by_id[store.plans[out.plan].opened_by])
        for ing in computed:
            out.ops += self.commit_ingest(ing, actor=actor, plan=out.plan, batch=out.plan, base=base,
                                          caused_by=caused_by)
        tail = list(bodies) + (strike_bodies(store, list(strikes), out.plan) if out.plan else [])
        if tail:
            out.ops += self.submit(tail, actor=actor, plan=out.plan, batch=out.plan, caused_by=caused_by)
        return out

    def must_propose(self, bid: str, actor: str, res: Ingest) -> str | None:
        """Why this machine write may not land on the trunk, or None.

        Public because a caller that sees a whole repair asks it per block and
        opens ONE plan for all of them (``reflect._amend``). Every machine
        write is gated by this function and no other copy of the rule."""
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
            # A translation replaces every word by design, so the deleted
            # fraction says nothing. Whose words they were decides.
            return "a translation of a person's words" if human else None
        return "a larger repair" if res.deleted_fraction > (GATE_HUMAN if human else GATE_MACHINE) else None

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
            lines = L.read_inbox(self.codoc_dir, client, self._inbox_offsets.get(client, 0))
            fresh: dict[str, L.Submission] = {}
            for line in lines:
                if line.sub is None:
                    L.ack(self.codoc_dir, client, line.cid, error=line.error)
                elif line.sub.cid not in self._acked.get(client, {}):
                    fresh.setdefault(line.sub.cid, line.sub)
            for got in self.apply_submissions(client, actor, list(fresh.values()), on_applied=lambda s, got: L.ack(
                    self.codoc_dir, client, s.cid, got.ops[0] if got.ops else None, error=got.conflict,
                    status=got.status)):
                out.extend(got.ops)
            if lines:
                self._inbox_offsets[client] = lines[-1].end
        L.write_offsets(self.codoc_dir, self._inbox_offsets)
        return out

    def apply_submissions(self, client: str, actor: str, subs: list[L.Submission],
                          on_applied=None) -> list["Applied"]:
        """Apply one client's submissions in order (``apply_submission``),
        then take back every plan this workspace opened for them that nothing
        landed under: the person would be asked for a verdict on nothing. A
        plan the client opened itself (a ``plan.open`` submission) is its own,
        and its ops may come in a later call. The daemon's drain and a
        daemonless writer (``daemon.client``) both come through here.
        ``on_applied(sub, applied)`` runs after each one, before the next: the
        drain acks there, so a crash between two leaves the first acked."""
        before = set(self.store.plans)
        out = []
        for s in subs:
            out.append(got := self.apply_submission(client, actor, s))
            if on_applied is not None:
                on_applied(s, got)
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
        self._apply(self._mint(K.PLAN_REJECT, {"pid": plan.id}, {}, actor=plan.actor, batch=plan.id))
        self._opened_plans = {k: v for k, v in self._opened_plans.items() if v != plan.id}

    def _as_proposal(self, actor: str, s: L.Submission) -> L.Submission:
        """A suggester's Backspace, as a proposal. A plan proposes and never
        kills, so a contributor's ``text.delete`` becomes the plan's
        ``suggest_del`` over the characters it names, unless they are all
        words the contributor's own open plan put there, which are theirs to
        take back. A ``segments`` delete names its characters exactly, and the
        proposal records exactly those (``chars``), so a word another actor
        typed between them is not struck."""
        if s.kind != K.TEXT_DELETE or s.plan or self.roles.role(actor) != SUGGEST:
            return s
        if own_plan_words(self.store, actor, deleted_ops(self.store, s.target, s.payload)):
            return s        # their own proposed words: a real delete, not a proposal to strike a proposal
        if "segments" in s.payload:
            payload = self._strike_segments(s.target, s.payload["segments"])
        elif "from" in s.payload and "to" in s.payload:
            payload = {"kind": SUGGEST_DEL, "attrs": {}, "start": s.payload["from"], "end": s.payload["to"], "on": True}
        else:
            return s
        if payload is None:
            return s        # names nothing live here: the applier reports why
        return L.Submission(cid=s.cid, kind=K.TEXT_MARK, target=dict(s.target), payload=payload,
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
            for p in reversed(list(self.store.open_plans.values())):
                if p.actor == actor and p.target == bid and p.kind == "edit":
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
        """The plan a contributor's trunk edit rides in: a suggester's
        untagged edit is applied as a plan of that actor. The daemon opens it, because the browser has no way to know it needs
        one, and one per submitted batch, because a plan per keystroke would be
        a verdict per keystroke. A comment needs none; anything the calculus
        cannot carry under a plan is left untagged for the policy to refuse."""
        if self.roles.role(actor) != SUGGEST or kind not in K.PLANNABLE:
            return None
        if is_comment_op(self.store, kind, target, payload):
            return None
        return self._plan_for(actor, batch, str(target.get("bid") or target.get("into") or ""), "suggested from the hub")

    def apply_submission(self, client: str, actor: str, s: L.Submission) -> "Applied":
        """Apply one client submission as ``actor``: what a client may assert,
        how a suggester's edit becomes a plan, and what an amend or an ingest
        answers are decided here and nowhere else. Total: a submission that
        raises is refused with the error. The first op minted is remembered
        under the client id, so a later ``["$cid", cid, k]`` anchor resolves."""
        acked = self._acked.setdefault(client, {})
        try:
            got = self._apply_one(actor, s, acked)
        except Exception as e:  # noqa: BLE001 (the applier is total; so is the drain)
            got = Applied(conflict=f"refused: {e!r}")
        if got.conflict is None and got.ops:
            got.conflict = got.ops[0].conflict
        acked[s.cid] = got.ops[0].id if got.ops else ""
        return got

    def _apply_one(self, actor: str, s: L.Submission, acked: dict[str, str]) -> "Applied":
        batch = s.batch or new_batch_id()
        if s.caused_by and actor_kind(actor) not in MACHINE_KINDS:
            # `caused_by` says "this op is what that hand-off produced" and the
            # provenance card and `settle` read it. A person's client does not
            # get to assert it about itself.
            s = replace(s, caused_by=None)
        s = replace(s, payload=client_payload(self.store, s.kind, s.payload, actor))
        if s.kind == "amend":
            # The amend decides its own plan, so no suggester plan is opened for it.
            p = s.payload
            done = self.amend(str(p.get("fid") or ""), actor=actor, note=str(p.get("note") or ""), caused_by=s.caused_by,
                              rewrites=[(r["bid"], r.get("path", "text"), r["new_md"], r.get("base_text"))
                                        for r in p.get("rewrites") or []],
                              bodies=p.get("bodies") or [], strikes=p.get("strikes") or [],
                              structural=bool(p.get("structural")))
            return Applied(done.ops, "; ".join(f"{b}: {why}" for b, why in done.refused) or None,
                           (f"plan:{done.plan}" if done.plan else "trunk") if done.ops else "unchanged")
        if s.kind != "ingest":
            # Resolve first: `_as_proposal` asks which plan minted the
            # characters a range names, and a `$cid` ref answers nothing. The
            # editor batches a flush, so an insert and the Backspace over it
            # arrive together exactly when somebody corrects a typo.
            s = replace(s, payload=_resolve_provisional(s.payload, acked, self._sent_text))
        s = self._as_proposal(actor, s)
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


def client_payload(store: Store, kind: str, payload: dict, actor: str = "") -> dict:
    """``payload`` as ``actor`` may send it.

    - Snapshot-only fields are removed, from an amend's bodies too; a body
      keeps only its kind, target and payload, since the amend decides the plan.
    - A mark that names its placer (``actor``/``op``/``at``) is honoured only
      as the inverse the applier recorded for one of the sender's own ops: an
      undo of their removal of a mark someone else placed. The recorded body
      is used, ``chars`` included. Any other mark is the sender's own, and a
      proposed deletion's ``chars`` are the ones the applier reads from its range."""
    out = {k: v for k, v in payload.items() if k not in K.SNAPSHOT_ONLY.get(kind, ())}
    if kind == "amend":
        out["bodies"] = [{"kind": b["kind"], "target": b.get("target") or {},
                          "payload": client_payload(store, b["kind"], b.get("payload") or {}, actor)}
                         for b in out.get("bodies") or []]
    if kind != K.TEXT_MARK:
        return out
    if any(k in out for k in ("actor", "op", "at")) and (recorded := _recorded_restore(store, actor, out)):
        return dict(recorded)
    for k in ("actor", "op", "at"):
        out.pop(k, None)
    if out.get("kind") == SUGGEST_DEL:
        out["attrs"] = {k: v for k, v in (out.get("attrs") or {}).items() if k != "chars"}
    return out


def _recorded_restore(store: Store, actor: str, payload: dict) -> dict | None:
    """The ``text.mark`` payload that an op of ``actor`` recorded in its
    inverse and that ``payload`` repeats (``chars`` aside), or None."""
    def key(p: dict) -> str:
        return json.dumps({**p, "attrs": {k: v for k, v in (p.get("attrs") or {}).items() if k != "chars"}},
                          sort_keys=True)
    want = key(payload)
    for o in reversed(store.ops):
        if o.actor == actor and o.conflict is None:
            for b in o.inverse:
                if b.get("kind") == K.TEXT_MARK and key(b.get("payload") or {}) == want:
                    return b["payload"]
    return None


def _resolve_provisional(payload: dict, minted: dict[str, str], sent: dict[str, str] | None = None) -> dict:
    """Replace ``["$cid", cid, k]`` anchors with ``[op_id, k]`` once the insert
    with that client id has been minted. An unresolvable one is left as is and
    fails as an unknown anchor rather than being guessed at.

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
