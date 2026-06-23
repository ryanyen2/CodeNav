"""Diagram plugin (U5) — a bidirectional, mostly-deterministic codec.

The diagram is a mermaid flowchart of a feature's bound code and its dependency
neighborhood. It demonstrates the *non-text* bidirectional round-trip that is the
core of the R2 rebuttal:

- ``lift`` (code → diagram): DETERMINISTIC. Render the 1-hop dependency graph of
  the feature's bound symbols (``codoc.graph.query``) to mermaid. No LLM. When code
  changes, Loop A re-runs this and the diagram refreshes *in place*.
- ``lower`` (diagram → code): the structured mapping is DETERMINISTIC — diff the
  old vs new mermaid edge set to see exactly which dependencies the human added or
  removed — and that delta becomes a precise directive the realizing agent applies
  (KTD8: structure is deterministic, transformation is the agent). An edit we
  cannot map to an edge delta (a freeform node, an unparseable diagram) returns a
  ``draft`` so it is held for confirmation rather than guessed at (KTD2).

Binding stays feature-level (KTD1): the diagram's symbols are the feature's bound
symbols; the plugin never binds code itself.
"""
from __future__ import annotations

import re

from codoc.blocks.base import (
    BindingMode,
    BlockPlugin,
    Capability,
    Dispatch,
    LiftContext,
    LiftResult,
    LowerContext,
    LowerResult,
)

# A mermaid edge line: `A --> B`, `A-->B`, `A --> |label| B` (label ignored).
_EDGE_RE = re.compile(r"^\s*([\w.:]+)\s*-->\s*(?:\|[^|]*\|\s*)?([\w.:]+)\s*$")


def _symbol_node(symbol_path: str) -> str:
    """A mermaid-safe node id for a symbol_path. Mermaid ids can't contain ``::``
    or ``.`` cleanly, so collapse to a leaf-ish token; collisions are acceptable
    for a per-feature neighborhood diagram (it is a picture, not the binding key)."""
    leaf = symbol_path.split("::", 1)[-1].replace(".", "_")
    return leaf or symbol_path


def _edges(content: str) -> set[tuple[str, str]]:
    """Parse the edge set from mermaid content (order-independent)."""
    out: set[tuple[str, str]] = set()
    for line in content.splitlines():
        m = _EDGE_RE.match(line)
        if m:
            out.add((m.group(1), m.group(2)))
    return out


def _has_unparseable_body(content: str) -> bool:
    """True if the content has non-trivial, non-edge lines (a freeform node, prose)
    we can't map to an edge delta — the signal to return a ``draft``."""
    for line in content.splitlines():
        s = line.strip()
        if not s or s.startswith(("flowchart", "graph", "%%")):
            continue
        if _EDGE_RE.match(line):
            continue
        # a bare node declaration like `A[Label]` or stray prose — not an edge
        return True
    return False


class DiagramPlugin(BlockPlugin):
    kind = "diagram"
    capabilities = frozenset({Capability.LIFT, Capability.LOWER})
    binding_mode = BindingMode.BOUND
    lift_dispatch = Dispatch.DETERMINISTIC
    lower_dispatch = Dispatch.AGENT  # the realizing agent applies the directive

    # ── code → diagram (deterministic) ──
    def lift(self, ctx: LiftContext) -> LiftResult:
        from codoc.graph.query import neighbors

        store = ctx.store
        symbols = [b.symbol_path for b in ctx.bindings]
        if store is None or not symbols:
            # Nothing to render (no graph handle or an ambient/unbound feature).
            return LiftResult.no_change()
        seeds = set(symbols)
        lines = ["flowchart TB"]
        seen: set[tuple[str, str]] = set()
        for sym in symbols:
            for dst in neighbors(store, sym, direction="out"):
                edge = (_symbol_node(sym), _symbol_node(dst))
                if edge in seen or edge[0] == edge[1]:
                    continue
                seen.add(edge)
                lines.append(f"  {edge[0]} --> {edge[1]}")
        # Isolated bound symbols (no internal edges) still appear as nodes so the
        # diagram reflects the whole feature, not only its coupled parts.
        for sym in symbols:
            node = _symbol_node(sym)
            if not any(node in e for e in seen):
                lines.append(f"  {node}")
        content = "\n".join(lines)
        prior = ctx.block.content if ctx.block else None
        return LiftResult.no_change() if content == prior else LiftResult.refresh(content)

    # ── diagram → code (deterministic delta → agent directive) ──
    def lower(self, ctx: LowerContext) -> LowerResult:
        new = ctx.new_block.content or ""
        old = ctx.old_block.content if ctx.old_block else ""
        if not ctx.bindings:
            return LowerResult.noop()  # ambient/unbound: a diagram edit implies no code
        old_edges, new_edges = _edges(old), _edges(new)
        added = new_edges - old_edges
        removed = old_edges - new_edges
        # If the human drew something we can't reduce to an edge delta, don't guess.
        if not added and not removed:
            if _has_unparseable_body(new) and new != old:
                return LowerResult.draft(
                    "The diagram changed in a way I can't map to a specific dependency "
                    "edit (a freeform node or relabel). Please confirm the intended "
                    "code change before I realize it.")
            return LowerResult.noop()
        parts: list[str] = []
        for s, d in sorted(removed):
            parts.append(f"Remove the dependency from `{s}` to `{d}` (the author deleted that edge).")
        for s, d in sorted(added):
            parts.append(f"Add a dependency/call from `{s}` to `{d}` (the author drew that edge).")
        return LowerResult.directive("\n".join(parts))
