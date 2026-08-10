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

from pydantic import BaseModel, Field, model_validator

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

# Provenance vocabulary — the change ledger (additive; "" = legacy/unknown).
#   actor: WHO made the change. "human", an agent id ("claude-code", "codex", …),
#          or "loop" (codoc's own deterministic machinery).
#   mode:  HOW it landed. "pen" = direct authoritative edit; "suggest" = proposed,
#          requires an accept verdict; "auto" = machine-applied safe maintenance.
#   caused_by: the directive (d-…), event (e-…), or suggestion id this change
#          implements — the causality chain that lets the IDE group a reflection
#          cascade under the doc edit that triggered it.
ACTOR_HUMAN = "human"
ACTOR_LOOP = "loop"
DEFAULT_AGENT_ACTOR = "claude-code"
MODE_PEN = "pen"
MODE_SUGGEST = "suggest"
MODE_AUTO = "auto"


def outranks(actor: str, other: str) -> bool:
    """Whether ``actor``'s edit wins over ``other``'s where the two contend.

    One rule: a person outranks anything that is not a person. The human is the
    author of intent; agents and the loop maintain an index of it. Where the two
    disagree about the same sentence the human is not proposing a change, they
    are correcting one, and making them accept their own words back through a
    review surface teaches them the tree argues with them.

    Deliberately NOT a graded scale. Ranking agents against the loop, or one
    agent against another, would invent an authority ordering nothing in the
    system can justify — and every level added is another way for the wrong side
    to win silently. Non-human sources tie, and a tie never overwrites: the
    caller keeps the incoming text as a proposal instead of guessing.

    An unknown actor ("" — a row written before provenance was recorded) ranks
    as non-human. That is the honest reading: it means codoc cannot show anyone
    who wrote this, which is not a claim to authority over someone who can.
    """
    return actor == ACTOR_HUMAN and other != ACTOR_HUMAN


def default_provenance(source: str, applied: bool) -> tuple[str, str]:
    """(actor, mode) inferred from a legacy ``source`` string — the back-compat
    bridge for call sites that don't (yet) carry explicit provenance."""
    if source == "user":
        return ACTOR_HUMAN, MODE_PEN
    if source in AGENT_SOURCES:
        return DEFAULT_AGENT_ACTOR, (MODE_AUTO if applied else MODE_SUGGEST)
    # loop_a / loop_b / bootstrap — codoc's own machinery
    return ACTOR_LOOP, (MODE_AUTO if applied else MODE_SUGGEST)

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
    # Sibling ORDER, given as neighbour identities rather than an index (MOVE_NODE
    # and ADD_NODE). An index is a re-derived positional guess — by the time the op
    # applies, another pass may have added or retired a sibling and "third child"
    # means something else — whereas "after A, before B" still means what its author
    # meant. Both empty = no opinion about order, which appends. Placing a node
    # FIRST is said by naming only what it goes before.
    after_id: str = ""             # the sibling this node follows ("" = none / first)
    before_id: str = ""            # the sibling this node precedes ("" = none / last)
    local_id: str = ""             # ADD_NODE: the webview's client-side node id (KTD8). Persisted on the
                                   #   minted feature so the host matches the minted fid back to the exact
                                   #   in-progress node (no title/order guessing → no duplicate/orphan adds).
    delete_code: bool = False      # RETIRE_NODE: True ⇒ also remove the bound code (explicit intent —
                                   #   an agent via MCP, or a human `~`); False ⇒ detach-only untrack
    # What an APPLIED amend displaced, recorded at the write boundary (apply_op) because
    # it is unrecoverable a moment later. A safe auto-amend never asks anyone — the loop
    # rewrites a description and the author finds out only if they happen to reread it —
    # so the IDE needs the previous wording to show WHAT changed, not merely that
    # something did. Same pattern as ADD_NODE pre-minting its feature_id: the event
    # records its own outcome rather than leaving it to be re-derived. Empty on a pending
    # proposal (nothing displaced yet) and on ops loaded from rows written before this.
    prev_description: str | None = None
    prev_written_by: str = ""      # authorship of the displaced prose ("human" | agent | "loop"),
                                   #   read BEFORE the write reassigns it — the IDE weights the
                                   #   cue by whether the loop overwrote a person's own words.


class Event(BaseModel):
    id: str = Field(default_factory=new_event_id)
    at: HLC = Field(default_factory=HLC.now)
    source: str  # "loop_a" | "loop_b" | "user" | "bootstrap" | "plan"
    op: NodeOp
    applied: bool = True  # False ⇒ pending proposal
    accepted_at: datetime | None = None
    # Change-ledger provenance ("" = legacy/unknown; see vocabulary above).
    actor: str = ""      # "human" | agent id | "loop"
    mode: str = ""       # "pen" | "suggest" | "auto"
    caused_by: str = ""  # directive/event/suggestion id this change implements

    @model_validator(mode="after")
    def _default_provenance(self) -> "Event":
        # Any construction path (apply_op, tests, tools) gets a sensible ledger
        # stamp; explicit actor/mode always win. Rows loaded from a legacy db
        # also pass through here, which is fine — the inferred values are
        # exactly what those events meant.
        if not self.actor and not self.mode:
            self.actor, self.mode = default_provenance(self.source, self.applied)
        return self

    @property
    def is_proposal(self) -> bool:
        return not self.applied
