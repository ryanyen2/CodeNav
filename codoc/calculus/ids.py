"""Identities.

An ``OpId`` is ``"<session>:<seq>"``: globally unique because a session id is
random and a session is written by exactly one daemon, stable because nothing
ever renumbers it (spec §2). A ``CharRef`` is ``(op, k)``: the k-th Unicode
scalar the op inserted. Blocks and plans carry random ids minted by whoever
creates them; the applier only checks uniqueness.

One JSON spelling for a character reference or an anchor, used everywhere a
ref crosses the wire or the log: ``["<op>", k]``, or the strings ``START`` /
``END``.
"""
from __future__ import annotations

import secrets

OpId = str
BlockId = str
PlanId = str
CharRef = tuple[OpId, int]

START = "START"   # anchor: before the first character / before the first block
END = "END"       # anchor: after the last character


def new_session_id() -> str:
    return "s-" + secrets.token_hex(6)


def new_block_id() -> str:
    return "b-" + secrets.token_hex(6)


def new_plan_id() -> str:
    return "p-" + secrets.token_hex(6)


def new_batch_id() -> str:
    return "t-" + secrets.token_hex(6)


def op_id(session: str, seq: int) -> OpId:
    return f"{session}:{seq}"


def ref_from_json(v) -> CharRef | str:
    """A ``CharRef`` or anchor keyword from its wire form. Raises ``ValueError``."""
    if v == START or v == END:
        return v
    if isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], str):
        return (v[0], int(v[1]))
    raise ValueError(f"not a character reference: {v!r}")


def ref_to_json(r: CharRef | str):
    return r if isinstance(r, str) else [r[0], r[1]]


# The actor grammar is closed: ``human:<who>``, ``agent:<which>``,
# ``translator:<lang>``, ``loop`` (the daemon's own passes) and ``import``
# (a v1 or Markdown import). Everything that decides by actor decides by kind.
ACTOR_KINDS = frozenset({"human", "agent", "translator", "loop", "import"})


def actor_kind(actor: str) -> str:
    """``human`` | ``agent`` | ``translator`` | ``loop`` | ``import`` | ``unknown``."""
    kind = actor.partition(":")[0]
    return kind if kind in ACTOR_KINDS else "unknown"


def is_human(actor: str) -> bool:
    return actor_kind(actor) == "human"


# Everything that is not a person. The authority policy gives these the trunk
# and plans but no verdict; ``ingest`` additionally refuses to let the ones that
# rewrite prose on their own — the loop, a translator, and a coding agent
# through ``codoc_reflect`` — drop a person's mark. An import writes a tree that
# has no marks yet, so it is not one of them.
MACHINE_KINDS = frozenset({"agent", "translator", "loop", "import"})
REWRITERS = frozenset({"agent", "loop", "translator"})
