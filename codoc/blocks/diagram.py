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

## What question this picture answers

A reader opens a diagram to ask one of Sillito's group-3 questions — *how are these
related?* — so the picture is judged by that and nothing else. Three things follow,
and each replaced an earlier choice that answered a different question:

- **Both directions.** The first version drew only the edges LEAVING the feature's
  symbols, which says what this feature uses and is silent on who uses it. Half of
  "how are these related" is the callers, and it is the half that decides whether a
  change here is safe, so incoming edges are drawn too.
- **Grouped by the feature that owns the code**, with that feature's title as the
  group label. A flat symbol graph makes the reader do the mapping from symbols back
  to intent, which is the mapping the tree exists to hold. Grouped, the picture reads
  as *this feature depends on those features* in the tree's own vocabulary, and a
  neighbour no feature covers is grouped by file and said to be outside the tree —
  which is itself worth seeing.
- **One node per symbol path.** Node ids used to collapse to the leaf name, so two
  files with a ``render`` became one box and the diagram drew an edge that does not
  exist in the code. A picture that invents a relationship is worse than no picture,
  so ids are derived from the whole symbol path and the leaf is only the label.

The neighborhood is capped (``MAX_NEIGHBOURS``), because a hub symbol's full 1-hop
graph is unreadable and unreadable answers the question no better than blank. The
highest-degree neighbours are kept, and the ones dropped are DRAWN as a count rather
than left implicit: a diagram that silently truncates reads as the whole story.
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
# A node declaration: `id["Label"]`, `id[Label]`, `id(Label)`. The lift emits the
# first form; the other two are what a person hand-writes.
_DECL_RE = re.compile(r"^\s*([\w.:]+)\s*[\[(]\s*\"?([^\"\])]*)\"?\s*[\])]\s*$")
_SUBGRAPH_RE = re.compile(r"^\s*subgraph\s+([\w.:]+)(?:\s*\[.*\])?\s*$", re.I)

# How many neighbouring symbols one picture can hold and still be read.
MAX_NEIGHBOURS = 12
# The node that reports what the cap left out.
_MORE_ID = "codoc_more"


def _node_id(symbol_path: str) -> str:
    """A mermaid-safe id for one symbol path, unique per path.

    Derived from the WHOLE path rather than its leaf, because the leaf is not
    unique: ``render`` appears in several files here, and collapsing them drew an
    edge between two functions that never call each other.
    """
    ident = re.sub(r"\W", "_", symbol_path or "")
    return ident if ident[:1].isalpha() or ident[:1] == "_" else f"n_{ident}"


def _leaf(symbol_path: str) -> str:
    return (symbol_path or "").split("::", 1)[-1] or symbol_path


def _quote(label: str) -> str:
    """A mermaid label. Double quotes delimit it, so they cannot appear inside."""
    return (label or "").replace('"', "'")


def _edges(content: str) -> set[tuple[str, str]]:
    """Parse the edge set from mermaid content (order-independent)."""
    out: set[tuple[str, str]] = set()
    for line in content.splitlines():
        m = _EDGE_RE.match(line)
        if m:
            out.add((m.group(1), m.group(2)))
    return out


def _declared_ids(content: str) -> set[str]:
    """Every id the content mentions: nodes, edge endpoints, and subgraphs.

    Subgraph containers count because they are ids an author can add, and the only
    use of this set is "was this id already here" -- a container the lift wrote has
    to cancel between old and new or every ordinary edge edit looks like the author
    invented four boxes.
    """
    out: set[str] = set()
    for line in (content or "").splitlines():
        m = _EDGE_RE.match(line)
        if m:
            out.update({m.group(1), m.group(2)})
            continue
        m = _SUBGRAPH_RE.match(line)
        if m:
            out.add(m.group(1))
            continue
        m = _DECL_RE.match(line)
        if m:
            out.add(m.group(1))
    return out


def _labels(content: str) -> dict[str, str]:
    """id -> the label written beside it, for the ids that declare one."""
    out: dict[str, str] = {}
    for line in (content or "").splitlines():
        if _SUBGRAPH_RE.match(line):
            continue
        m = _DECL_RE.match(line)
        if m and m.group(2).strip():
            out[m.group(1)] = m.group(2).strip()
    return out


def _has_unparseable_body(content: str) -> bool:
    """True if a line is not something this codec emits or can read.

    Node declarations and subgraphs USED to count as unparseable, because the lift
    emitted neither. It emits both now, so the signal moved: what marks an edit as
    unmappable is a box the author ADDED that resolves to no symbol, which ``lower``
    decides by comparing ids -- not the presence of a declaration.
    """
    for line in (content or "").splitlines():
        s = line.strip()
        if not s or s.startswith(("flowchart", "graph", "%%")):
            continue
        if s.lower() == "end" or _SUBGRAPH_RE.match(line):
            continue
        if _EDGE_RE.match(line) or _DECL_RE.match(line):
            continue
        return True
    return False


def _symbol_map(ctx: LowerContext) -> dict[str, str]:
    """id → symbol path, rebuilt from the same source the lift drew from.

    Nothing is stored in the diagram to carry this. Both halves derive the mapping
    from the feature's bindings plus their 1-hop neighbours, so the round trip is
    symmetric by construction rather than by a comment in the content that an author
    could delete while editing.
    """
    out: dict[str, str] = {}
    symbols = [b.symbol_path for b in (ctx.bindings or [])]
    for sym in symbols:
        out[_node_id(sym)] = sym
    store = ctx.store
    if store is None:
        return out
    try:
        from codoc.graph.query import neighbors

        for sym in symbols:
            for other in neighbors(store, sym, direction="both"):
                out.setdefault(_node_id(other), other)
    except Exception:  # noqa: BLE001 — no graph handle is not a reason to fail an edit
        pass
    return out


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
        symbols = sorted({b.symbol_path for b in ctx.bindings})
        if store is None or not symbols:
            # Nothing to render (no graph handle or an ambient/unbound feature).
            return LiftResult.no_change()
        own = set(symbols)

        edges: set[tuple[str, str]] = set()
        for sym in symbols:
            for dst in neighbors(store, sym, direction="out"):
                if dst and dst != sym:
                    edges.add((sym, dst))
            # The callers. Skipped when the caller is also this feature's own code,
            # since the outward pass already drew that edge.
            for src in neighbors(store, sym, direction="in"):
                if src and src != sym and src not in own:
                    edges.add((src, sym))

        edges, dropped = self._cap(edges, own)
        content = self._render(ctx, own, edges, dropped)
        prior = ctx.block.content if ctx.block else None
        return LiftResult.no_change() if content == prior else LiftResult.refresh(content)

    # ── the neighborhood, bounded ──
    @staticmethod
    def _cap(
        edges: set[tuple[str, str]], own: set[str],
    ) -> tuple[set[tuple[str, str]], int]:
        """Keep the neighbours most tied to this feature, and say how many went.

        Degree first, then the symbol path, so the same graph always yields the same
        picture — an arbitrary truncation would make every lift look like a change
        and refresh the block forever.
        """
        foreign: dict[str, int] = {}
        for src, dst in edges:
            for sym in (src, dst):
                if sym not in own:
                    foreign[sym] = foreign.get(sym, 0) + 1
        if len(foreign) <= MAX_NEIGHBOURS:
            return edges, 0
        keep = {
            sym for sym, _n in
            sorted(foreign.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_NEIGHBOURS]
        }
        kept = {(s, d) for s, d in edges
                if (s in own or s in keep) and (d in own or d in keep)}
        return kept, len(foreign) - len(keep)

    # ── the picture ──
    def _render(
        self,
        ctx: LiftContext,
        own: set[str],
        edges: set[tuple[str, str]],
        dropped: int,
    ) -> str:
        nodes = set(own) | {s for e in edges for s in e}
        groups = self._group(ctx, nodes, own)

        lines = ["flowchart TB"]
        for gid, (label, members) in groups:
            lines.append(f'  subgraph {gid}["{_quote(label)}"]')
            for sym in members:
                lines.append(f'    {_node_id(sym)}["{_quote(_leaf(sym))}"]')
            lines.append("  end")
        if dropped:
            lines.append(f'  {_MORE_ID}["+{dropped} more related symbols"]')
        for src, dst in sorted(edges):
            lines.append(f"  {_node_id(src)} --> {_node_id(dst)}")
        return "\n".join(lines)

    @staticmethod
    def _group(
        ctx: LiftContext, nodes: set[str], own: set[str],
    ) -> list[tuple[str, tuple[str, list[str]]]]:
        """Nodes grouped by the feature that owns them, this feature first.

        A neighbour bound to no feature is grouped by its file and labelled as
        outside the tree. That is not a fallback for tidiness: code the tree does not
        cover is exactly what a reader wants to notice in a picture of what this
        feature touches.
        """
        store = ctx.store
        by_group: dict[tuple[str, str], list[str]] = {}
        labels: dict[tuple[str, str], str] = {}
        mine = ("feature", ctx.feature.id)
        labels[mine] = ctx.feature.title or "This feature"

        for sym in sorted(nodes):
            if sym in own:
                by_group.setdefault(mine, []).append(sym)
                continue
            key, label = None, ""
            file = sym.split("::", 1)[0]
            try:
                binding = store.binding_at(file, sym) if store is not None else None
                holder = store.get_feature(binding.feature_id) if binding else None
            except Exception:  # noqa: BLE001 — an unreadable graph must not lose the node
                holder = None
            if holder is not None and not holder.retired:
                key = ("feature", holder.id)
                label = holder.title or holder.id
            else:
                key = ("file", file)
                label = f"{file} (not in the tree)"
            labels.setdefault(key, label)
            by_group.setdefault(key, []).append(sym)

        ordered = [mine] if mine in by_group else []
        ordered += sorted(
            (k for k in by_group if k != mine),
            key=lambda k: (k[0] != "feature", labels[k]))
        out = []
        for key in ordered:
            gid = _node_id(f"{key[0]}_{key[1]}")
            out.append((gid, (labels[key], by_group[key])))
        return out

    # ── diagram → code (deterministic delta → agent directive) ──
    def lower(self, ctx: LowerContext) -> LowerResult:
        new = ctx.new_block.content or ""
        old = ctx.old_block.content if ctx.old_block else ""
        if not ctx.bindings:
            return LowerResult.noop()  # ambient/unbound: a diagram edit implies no code
        old_edges, new_edges = _edges(old), _edges(new)
        added = new_edges - old_edges
        removed = old_edges - new_edges
        known = _symbol_map(ctx)

        if added or removed:
            return LowerResult.directive(
                self._delta_directive(added, removed, known, _labels(new)))

        # No edge delta. The author changed something the codec cannot reduce to a
        # dependency, so the question is whether they changed anything that IMPLIES
        # code at all. Reordering or regrouping does not; a new box does, and so does
        # renaming one, and neither can be turned into an instruction without knowing
        # which symbol is meant -- so both are held (KTD2) rather than guessed at.
        if new == old:
            return LowerResult.noop()
        old_labels, new_labels = _labels(old), _labels(new)
        invented = sorted(
            _declared_ids(new) - _declared_ids(old) - set(known) - {_MORE_ID})
        renamed = sorted(
            i for i in set(old_labels) & set(new_labels)
            if old_labels[i] != new_labels[i])
        if invented:
            return LowerResult.draft(
                "The diagram now has "
                + ", ".join(f"`{new_labels.get(i, i)}`" for i in invented)
                + " on it with no edge to or from it, so I can't tell what code "
                  "change is being asked for. Draw the dependency, or say which "
                  "code you mean.")
        if renamed:
            return LowerResult.draft(
                "The diagram renames "
                + ", ".join(f"`{old_labels[i]}` to `{new_labels[i]}`" for i in renamed)
                + ". Confirm whether that is a rename in the code before I realize it.")
        if _has_unparseable_body(new):
            return LowerResult.draft(
                "The diagram changed in a way I can't map to a specific dependency "
                "edit. Please confirm the intended code change before I realize it.")
        return LowerResult.noop()

    @staticmethod
    def _delta_directive(
        added: set[tuple[str, str]],
        removed: set[tuple[str, str]],
        known: dict[str, str],
        labels: dict[str, str],
    ) -> str:
        """The edge delta, in words, naming each end as precisely as it can be named.

        A symbol path when the id resolves to one; else the label the author wrote,
        which is their own words for a box that has no code behind it yet -- an edge
        drawn to something that does not exist is a request to create it, and quoting
        the sanitized id at an agent instead would hand it a mangled string to search
        for.
        """
        def name(node_id: str) -> str:
            return known.get(node_id) or labels.get(node_id) or node_id

        parts: list[str] = []
        for s, d in sorted(removed):
            parts.append(f"Remove the dependency from `{name(s)}` to `{name(d)}` "
                         f"(the author deleted that edge).")
        for s, d in sorted(added):
            parts.append(f"Add a dependency/call from `{name(s)}` to `{name(d)}` "
                         f"(the author drew that edge).")
        return "\n".join(parts)
