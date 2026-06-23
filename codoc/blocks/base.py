"""The block-plugin contract — the minimal thing a medium declares to round-trip.

"Bidirectional codec" is the *maximal* shape. Most media implement a subset, and
the contract's whole job is to let a plugin *declare which directions it
supports* so the loops dispatch only those (KTD5). A plugin declares any subset
of three capabilities:

- :attr:`Capability.LIFT`  — code → block. Loop A refreshes the block when bound
  code changes. **Read-only on code, idempotent, ungated** (it is attribution and
  runs even on a held feature — see the change ledger).
- :attr:`Capability.LOWER` — block → code. Loop B turns a human edit into a
  realize *directive*. **Never mutates code directly; hold-gated; ambiguous →
  held draft.**
- :attr:`Capability.CONSULT` — block → realization context. The block is passive
  input the agent reads before implementing (the existing ``Consult:`` / WebFetch
  mechanism). No round-trip.

A medium is honest about its arrows: a website URL is ``CONSULT``-only; a UI
screenshot is ``LIFT`` + ``CONSULT`` but never ``LOWER``; a diagram is ``LIFT`` +
``LOWER``. ``CONSULT``-only + ``ambient`` is the cheapest medium to add — no codec
to write.

The two safety rules that keep the attribution/intent split (KTD2) intact no
matter what a plugin author writes:

1. ``lift`` may only *read* code and return content; it must not emit a directive.
2. ``lower`` may only emit a directive/draft; it must not mutate code.

These are encoded by the *types*: ``lift`` returns a :class:`LiftResult` (content
only), ``lower`` returns a :class:`LowerResult` (directive/draft/no-op only).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from codoc.model.binding import Binding
from codoc.model.block import Block, BlockLifecycle
from codoc.model.feature import Feature


class Capability(str, Enum):
    LIFT = "lift"        # code → block (attribution; ungated, read-only on code)
    LOWER = "lower"      # block → code directive (intent; hold-gated, lossy→draft)
    CONSULT = "consult"  # block → realization context (passive, no round-trip)


class BindingMode(str, Enum):
    BOUND = "bound"      # binding view derives from the parent feature's bindings (KTD1)
    AMBIENT = "ambient"  # no binding; pure human context / consultation


class Dispatch(str, Enum):
    DETERMINISTIC = "deterministic"  # a pure codec in code (e.g. dep-graph → mermaid)
    AGENT = "agent"                  # a declared prompt the loop hands to the agent


# ── codec results ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class LiftResult:
    """Outcome of ``lift`` (code → block). Content-only by construction — a plugin
    cannot reach code from here. ``changed=False`` means "bound code did not move
    this block" (a no-op the loop skips)."""

    changed: bool
    content: str | None = None

    @classmethod
    def no_change(cls) -> "LiftResult":
        return cls(changed=False)

    @classmethod
    def refresh(cls, content: str) -> "LiftResult":
        return cls(changed=True, content=content)


@dataclass(frozen=True)
class LowerResult:
    """Outcome of ``lower`` (block → code). Directive/draft/no-op only — a plugin
    cannot mutate code from here, it can only *propose* a change.

    - ``directive`` — a confident realize directive, queued for the session.
    - ``draft``     — ambiguous/lossy; held for human confirmation (the draft gate).
    - ``noop``      — the edit does not imply a code change."""

    kind: str          # "directive" | "draft" | "noop"
    text: str = ""

    @classmethod
    def directive(cls, text: str) -> "LowerResult":
        return cls(kind="directive", text=text)

    @classmethod
    def draft(cls, text: str) -> "LowerResult":
        return cls(kind="draft", text=text)

    @classmethod
    def noop(cls) -> "LowerResult":
        return cls(kind="noop")


@dataclass(frozen=True)
class LiftContext:
    """Everything a ``lift`` needs. ``code_context`` is an opaque blob the loop
    assembles (bound chunk text, graph neighborhood) — the plugin interprets it.
    ``block`` is the prior block (None when the loop is offering to *create* one).
    ``store`` is a READ-ONLY handle for plugins that need graph/index queries (the
    diagram plugin reads the dependency graph); a plugin must never mutate it."""

    feature: Feature
    bindings: list[Binding]
    code_context: str = ""
    block: Block | None = None
    store: object | None = None


@dataclass(frozen=True)
class LowerContext:
    """Everything a ``lower`` needs: the prior + edited block and the feature's
    binding context. The structural diff has already established that ``new_block``
    is the same identity as ``old_block`` (same id) with changed content (KTD8) —
    the plugin only has to translate the *content* delta to a directive. ``store``
    is a READ-ONLY handle (graph/index queries); never mutate it."""

    feature: Feature
    old_block: Block | None
    new_block: Block
    bindings: list[Binding]
    code_context: str = ""
    store: object | None = None


# ── the plugin ──────────────────────────────────────────────────────────--
class BlockPlugin:
    """Base class for a medium plugin. Subclasses set the class-level declaration
    fields and override exactly the methods for the capabilities they declare; the
    registry enforces that correspondence at registration time.

    Subclass contract::

        class DiagramPlugin(BlockPlugin):
            kind = "diagram"
            capabilities = frozenset({Capability.LIFT, Capability.LOWER})
            binding_mode = BindingMode.BOUND
            lift_dispatch = Dispatch.DETERMINISTIC
            lower_dispatch = Dispatch.AGENT
            def lift(self, ctx): ...
            def lower(self, ctx): ...
    """

    # ── declaration (override on the subclass) ──
    kind: str = ""
    capabilities: frozenset[Capability] = frozenset()
    binding_mode: BindingMode = BindingMode.BOUND
    lifecycle: BlockLifecycle = BlockLifecycle.PERSISTENT
    lift_dispatch: Dispatch = Dispatch.DETERMINISTIC
    lower_dispatch: Dispatch = Dispatch.AGENT

    # ── capability methods (override only what you declare) ──
    def lift(self, ctx: LiftContext) -> LiftResult:  # noqa: ARG002
        raise NotImplementedError(f"{self.kind}: declared LIFT but did not implement lift()")

    def lower(self, ctx: LowerContext) -> LowerResult:  # noqa: ARG002
        raise NotImplementedError(f"{self.kind}: declared LOWER but did not implement lower()")

    def consult(self, block: Block) -> str:  # noqa: ARG002
        raise NotImplementedError(f"{self.kind}: declared CONSULT but did not implement consult()")

    # ── convenience ──
    def has(self, cap: Capability) -> bool:
        return cap in self.capabilities
