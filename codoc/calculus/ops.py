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

# Ops a plan may carry. A plan proposes and never destroys: it asks for words
# to go with ``suggest_del`` marks, so ``block.delete`` and ``block.merge`` are
# not here. ``bind.attach`` is here because a proposed node's bindings are part
# of the proposal: rejecting it detaches them. ``text.delete`` and
# ``text.restore`` are here only for a plan's own words (the handler holds them
# to those), so a suggester can take back what they proposed without an
# untagged op.
PLANNABLE = frozenset({TEXT_INSERT, TEXT_DELETE, TEXT_RESTORE, TEXT_MARK,
                       BLOCK_ADD, BLOCK_SET, BLOCK_SPLIT, BLOCK_MOVE, BIND_ATTACH})

# Ops only an ``owner`` or ``maintainer`` may write: verdicts and hand-offs.
# ``feature.realized`` is also the loop's, which settles it on evidence.
OWNER_ONLY = frozenset({PLAN_ACCEPT, PLAN_REJECT, PLAN_REOPEN, FEATURE_HANDOFF, FEATURE_REALIZED})

# A plan's verdicts and their undo: the ops that turn a plan's status, and the
# ones whose canonical payload carries ``steps``.
TURNS = frozenset({PLAN_ACCEPT, PLAN_REJECT, PLAN_REOPEN})

# Ops that change the set of plans or a plan's status, whatever blocks they touch.
PLAN_KINDS = TURNS | {PLAN_OPEN}

STRUCTURAL = frozenset({BLOCK_ADD, BLOCK_SPLIT, BLOCK_MERGE, BLOCK_MOVE, BLOCK_DELETE, BLOCK_RESTORE})

# Payload fields only a snapshot writes: a plan's archived ops, text with its
# original authors, who added a block, its death, its detached bindings, the
# values the author's unrealized sets replaced, its watermark clocks and the move
# that ranked it; a hand-off's `realized`, which makes it the restore of a whole
# watermark. The
# applier honours them so a fold can rebuild state; from any other writer they
# would let one actor claim another's words or ops.
SNAPSHOT_ONLY = {
    BLOCK_ADD: ("texts", "added_by", "dead", "dead_actor", "dead_at", "dead_in", "detached", "prior", "bound_once",
                "code_at", "realized_at", "ranked_by", "void"),
    PLAN_OPEN: ("ops", "opened_by"),
    PLAN_ACCEPT: ("verdict",),
    PLAN_REJECT: ("verdict",),
    FEATURE_HANDOFF: ("realized",),
}


# The payload keys that hold a character anchor.
ANCHOR_KEYS = ("after", "from", "to", "start", "end", "at")


class Conflict(Exception):
    """An op that cannot apply. ``code`` is the stable identifier the log
    records (docs/model.md, Conflict codes)."""
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code


class OpBody(TypedDict):
    """An op without identity, provenance or tag: what an inverse records and
    what a client submits. An inverse is a list of bodies applied in order (a
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


def moved_code(kind: str, payload: dict) -> bool:
    """Whether an applied op, by its canonical payload, says code its feature
    already had changed: a detach, or an attach or refresh whose hashes differ
    from the binding's previous ones. Drift's clock (``code_at``) moves on
    exactly these. An attach that took a symbol is new coverage or a
    relocation, not a change to code the prose describes; one that sets the
    hashes a binding already had records who reviewed the code."""
    if kind == BIND_DETACH:
        return True
    if kind not in (BIND_ATTACH, BIND_REFRESH) or "prev_fingerprint" not in payload:
        return False
    return (payload.get("fingerprint", ""), payload.get("types_hash", "")) \
        != (payload.get("prev_fingerprint", ""), payload.get("prev_types_hash", ""))


def built_code(kind: str, payload: dict) -> bool:
    """Whether an applied op is evidence of a build: it moved code
    (``moved_code``), or it is an attach that took a symbol, binding code the
    build wrote."""
    return moved_code(kind, payload) or kind == BIND_ATTACH and "prev_fingerprint" not in payload


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
