"""Block — a typed-medium projection of (part of) a feature's intent.

A :class:`~codoc.model.feature.Feature` is the unit of authored intent and owns
the canonical prose (``description``). A **block** generalizes that node into
*typed media*: a diagram, a UI/bug screenshot, a LaTeX formula, a code/algorithm
sketch, an external reference URL. Each block is backed by a plugin (see
:mod:`codoc.blocks.base`) that defines a bidirectional codec between the medium
and code.

Two design rules carried from the plan (`docs/plans/2026-06-22-001-...`):

- **KTD1 — binding stays feature-level.** A block does NOT bind code
  independently; its binding *view* is derived from its parent feature's
  bindings. The ``UNIQUE(file, symbol_path)`` anti-duplication invariant is left
  untouched. ``ambient`` blocks (a bug screenshot, a reference URL) have an empty
  binding view by construction.

- **KTD8 — identity is deterministic and content-independent.** ``id`` is
  assigned once and never re-derived from content, so a block's identity survives
  arbitrary host edits (move = ``ord`` change, delete+undo, heading↔paragraph).
  The loops diff the settled block-id set against a stable baseline; the LLM only
  *transforms content* for blocks the structural diff already identified — it
  never has to track identity.

Prose is the implicit **block-zero**: it is NOT stored here, it remains
``feature.description``. Only non-prose media are rows. This keeps every existing
feature (zero blocks) working without migration.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from codoc.model.hlc import HLC
from codoc.model.ids import new_block_id


class BlockLifecycle(str, Enum):
    """How long a block lives in the document (KTD3/KTD4).

    ``PERSISTENT`` — lives in the doc body; refreshed in place by ``lift`` when
      bound code changes (a UI-state screenshot, a module diagram, a formula).
    ``TRANSIENT`` — rides the one-shot steering channel; consumed by realization
      and discarded on the next render (a bug screenshot in a comment thread). A
      transient block is never a durable part of the tree."""

    PERSISTENT = "persistent"
    TRANSIENT = "transient"


class Provenance(str, Enum):
    """Who authored the block's current content. Lets a host decorate
    agent-derived content distinctly and lets the loops respect human authorship
    (doc-wins) without inferring it from a diff."""

    HUMAN = "human"        # the user authored/edited it
    AGENT = "agent"        # a realization wrote it
    DERIVED = "derived"    # deterministically lifted from code (e.g. dep-graph diagram)


class Block(BaseModel):
    """One typed-medium block on a feature.

    ``content`` is *opaque to the core* — its meaning belongs to the plugin named
    by ``kind`` (mermaid source for a diagram, an attachment ref for an image, a
    URL for a reference). The core only stores, orders, and identity-tracks it.
    """

    id: str = Field(default_factory=new_block_id)
    feature_id: str
    kind: str                                   # plugin key: "diagram" | "image" | "latex" | "url" | …
    content: str = ""                           # opaque payload; the plugin owns its schema
    lifecycle: BlockLifecycle = BlockLifecycle.PERSISTENT
    provenance: Provenance = Provenance.HUMAN
    ord: int = 0                                # position within the feature; a move is an ord change
    created_at: HLC = Field(default_factory=HLC.now)
    updated_at: HLC = Field(default_factory=HLC.now)
