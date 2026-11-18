"""The ``Op`` record and the op vocabulary (docs/model.md, Operations).

An op on the wire carries its target and payload as the client wrote them.
The applier fills ``payload`` with the canonical form (a delete's explicit
segments, a move's computed range), ``inverse`` with the op bodies that undo
it, and ``conflict`` when it could not be applied. The stored record is the
truth; the wire form is what it was before apply.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict

from codoc.calculus.ids import OpId

# Text
TEXT_INSERT = "text.insert"
TEXT_DELETE = "text.delete"
TEXT_RESTORE = "text.restore"
TEXT_MARK = "text.mark"
# Blocks
BLOCK_ADD = "block.add"
BLOCK_SET = "block.set"
BLOCK_SPLIT = "block.split"
BLOCK_MERGE = "block.merge"
BLOCK_MOVE = "block.move"
BLOCK_DELETE = "block.delete"
BLOCK_RESTORE = "block.restore"
# Bindings
BIND_ATTACH = "bind.attach"
BIND_DETACH = "bind.detach"
BIND_REFRESH = "bind.refresh"
# Plans
PLAN_OPEN = "plan.open"
PLAN_ACCEPT = "plan.accept"
PLAN_REJECT = "plan.reject"
PLAN_REOPEN = "plan.reopen"      # the inverse of a verdict; reaches the log only through undo
# Watermarks
FEATURE_HANDOFF = "feature.handoff"
FEATURE_REALIZED = "feature.realized"

KINDS = frozenset({
    TEXT_INSERT, TEXT_DELETE, TEXT_RESTORE, TEXT_MARK,
    BLOCK_ADD, BLOCK_SET, BLOCK_SPLIT, BLOCK_MERGE, BLOCK_MOVE, BLOCK_DELETE, BLOCK_RESTORE,
    BIND_ATTACH, BIND_DETACH, BIND_REFRESH,
    PLAN_OPEN, PLAN_ACCEPT, PLAN_REJECT, PLAN_REOPEN,
    FEATURE_HANDOFF, FEATURE_REALIZED,
})

# Ops a plan may carry. A plan proposes and never destroys: its deletions are
# ``suggest_del`` marks, so ``text.delete``, ``block.delete`` and ``block.merge``
# are not here. ``bind.attach`` is here because a proposed node's bindings are
# part of the proposal: rejecting it detaches them. Each kind here has an
# inverse that ``plan.reject`` applies.
# `text.delete` and `text.restore` are here for one case only, and the handler
# enforces it: a plan taking back words IT proposed. That is the proposal
# getting smaller, not a second act on the trunk, and it is why a suggester
# needs no untagged op at all. A plan may not delete anybody else's
# characters: `suggest_del` is how a proposal asks for that.
PLANNABLE = frozenset({TEXT_INSERT, TEXT_DELETE, TEXT_RESTORE, TEXT_MARK,
                       BLOCK_ADD, BLOCK_SET, BLOCK_SPLIT, BLOCK_MOVE, BIND_ATTACH})

# Verdicts and hand-offs: ``owner`` or ``maintainer`` only. ``feature.realized``
# is emitted by the daemon on evidence, never by an agent; the shipped policy
# denies it to every ``agent:*`` actor.
VERDICTS = frozenset({PLAN_ACCEPT, PLAN_REJECT, PLAN_REOPEN, FEATURE_HANDOFF, FEATURE_REALIZED})

# Ops that change the set of plans or a plan's status, whatever blocks they touch.
PLAN_KINDS = frozenset({PLAN_OPEN, PLAN_ACCEPT, PLAN_REJECT, PLAN_REOPEN})

STRUCTURAL = frozenset({BLOCK_ADD, BLOCK_SPLIT, BLOCK_MERGE, BLOCK_MOVE, BLOCK_DELETE, BLOCK_RESTORE})

# Payload fields only a snapshot writes: a plan's archived ops, text with its
# original authors, a block's death, its detached bindings and its watermarks.
# The applier honours them so a fold can rebuild state; from any other writer
# they would let one actor claim another's words or ops.
SNAPSHOT_ONLY = {
    BLOCK_ADD: ("texts", "dead", "dead_actor", "dead_at", "dead_in", "detached", "bound_once", "code_at", "realized_at"),
    PLAN_OPEN: ("ops", "opened_by"),
    PLAN_ACCEPT: ("verdict",),
    PLAN_REJECT: ("verdict",),
}


class OpBody(TypedDict):
    """An op without identity, provenance or tag: what an inverse records and
    what a client submits. An inverse is a list of bodies applied IN ORDER (a
    displacing attach undoes as "detach, then re-attach the prior owner"). An
    undo is the person's own untagged op, so an inverse carries no ``plan`` and
    no ``caused_by``."""
    kind: str
    target: dict
    payload: dict


def body(kind: str, target: dict, payload: dict) -> OpBody:
    return {"kind": kind, "target": dict(target), "payload": dict(payload)}


@dataclass
class Op:
    id: OpId
    at: str                     # HLC string, lexicographically ordered
    actor: str
    batch: str
    kind: str
    target: dict = field(default_factory=dict)
    payload: dict = field(default_factory=dict)
    base: dict = field(default_factory=dict)       # Frontier {session: seq}
    plan: str | None = None
    caused_by: str | None = None
    inverse: list[OpBody] = field(default_factory=list)
    conflict: str | None = None

    def to_json(self) -> dict:
        d = {
            "id": self.id, "at": self.at, "actor": self.actor, "batch": self.batch,
            "kind": self.kind, "target": self.target, "payload": self.payload,
        }
        if self.base:
            d["base"] = self.base
        if self.plan:
            d["plan"] = self.plan
        if self.caused_by:
            d["caused_by"] = self.caused_by
        if self.inverse:
            d["inverse"] = self.inverse
        if self.conflict:
            d["conflict"] = self.conflict
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Op":
        return cls(
            id=d["id"], at=d["at"], actor=d["actor"], batch=d.get("batch", ""),
            kind=d["kind"], target=dict(d.get("target") or {}), payload=dict(d.get("payload") or {}),
            base=dict(d.get("base") or {}), plan=d.get("plan"), caused_by=d.get("caused_by"),
            inverse=list(d.get("inverse") or []), conflict=d.get("conflict"),
        )


def frontier_union(a: dict, b: dict) -> dict:
    """Per-session max: the monotone merge of two frontiers (spec I9)."""
    out = dict(a)
    for s, n in b.items():
        if n > out.get(s, -1):
            out[s] = n
    return out


def in_frontier(op_id: OpId, frontier: dict) -> bool:
    session, _, seq = op_id.rpartition(":")
    return int(seq) <= frontier.get(session, -1)


def covered(handoff: dict, realized: dict) -> bool:
    """Every op the hand-off frontier names is inside the realized frontier:
    the definition of "nothing queued" that every caller uses."""
    return all(realized.get(s, -1) >= n for s, n in handoff.items())
