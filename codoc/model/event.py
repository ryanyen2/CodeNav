"""Event + NodeOp — the single append-only change record.

One ``Event`` is logged per applied or proposed tree mutation, replacing the old
18 transaction kinds. A *proposal* is simply an Event with ``applied=False``;
accepting it flips ``applied`` to True and runs the op against the store.

The op taxonomy is deliberately tiny and split into two tiers:

* **safe** — ``ATTACH / DETACH / REFRESH / AMEND`` — auto-applied (AMEND only
  when the edit is small; see ``loop.apply.AMEND_SAFE_RATIO``).
* **structural** — ``ADD_NODE / MOVE_NODE / RETIRE_NODE`` — surfaced as a
  reviewable hunk in the ``.codoc`` file before they take effect.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from codoc.model.hlc import HLC
from codoc.model.ids import new_event_id


class NodeOpKind(str, Enum):
    # safe — auto-applied
    ATTACH = "attach"        # bind a (file, symbol_path) to an existing feature
    DETACH = "detach"        # remove a binding whose symbol is gone
    REFRESH = "refresh"      # update a binding's fingerprint; no semantic change
    AMEND = "amend"          # edit a feature's title/description
    # structural — reviewed
    ADD_NODE = "add_node"    # introduce a new feature
    MOVE_NODE = "move_node"  # reparent a feature (uplift / restructure)
    RETIRE_NODE = "retire_node"  # retire a feature


# Event.source constants — plain strings (no Pydantic enum so no migration needed)
PLAN_SOURCE = "plan"                  # agent-authored intent proposal (plan before code)
LOOP_A_AGENT_SOURCE = "loop_a_agent"  # agent-driven reflection via MCP (code-first loop)
AGENT_SOURCES = frozenset({PLAN_SOURCE, LOOP_A_AGENT_SOURCE})  # sources from an AI agent

SAFE_OPS: frozenset[NodeOpKind] = frozenset(
    {NodeOpKind.ATTACH, NodeOpKind.DETACH, NodeOpKind.REFRESH, NodeOpKind.AMEND}
)
STRUCTURAL_OPS: frozenset[NodeOpKind] = frozenset(
    {NodeOpKind.ADD_NODE, NodeOpKind.MOVE_NODE, NodeOpKind.RETIRE_NODE}
)


class NodeOp(BaseModel):
    kind: NodeOpKind
    feature_id: str | None = None  # target feature; None for ADD_NODE (id minted on apply)
    parent_id: str | None = None   # for ADD_NODE / MOVE_NODE
    title: str | None = None       # for ADD_NODE / AMEND
    description: str | None = None  # for ADD_NODE / AMEND
    bindings: list[tuple[str, str]] = Field(default_factory=list)  # (file, symbol_path)
    rationale: str = ""            # one-line justification, shown in proposal hunks
    realized: bool | None = None   # ADD_NODE realization (None ⇒ default True); False = plan placeholder


class Event(BaseModel):
    id: str = Field(default_factory=new_event_id)
    at: HLC = Field(default_factory=HLC.now)
    source: str  # "loop_a" | "loop_b" | "user" | "bootstrap" | "plan"
    op: NodeOp
    applied: bool = True  # False ⇒ pending proposal
    accepted_at: datetime | None = None

    @property
    def is_proposal(self) -> bool:
        return not self.applied
