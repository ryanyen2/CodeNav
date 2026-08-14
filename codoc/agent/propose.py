"""``codoc propose`` — author an agent plan proposal in the codoc feature tree.

Creates a ``applied=False`` ``Event`` (a *pending proposal*) using the same
``apply_op`` seam that Loop A uses for structural proposals.  The only
difference from a code-drift proposal is ``source="plan"`` — so the IDE's
``Accept / Reject`` flow and Loop B's directive-building are completely
unchanged.

Usage (from the SKILL.md or a test)::

    from codoc.agent.propose import propose_plan
    event_id = propose_plan(
        root_dir,
        kind="add_node",
        title="Date formatting",
        description="Formats dates as ISO-8601 strings throughout the app.",
        binds=["utils/dates.py::format_date"],
    )

This call:
1. Constructs a ``NodeOp``.
2. Calls ``apply_op(op, store, source="plan", applied=False)`` — the Event is
   logged with ``applied=False`` so it appears in ``store.pending_events()``.
3. Calls ``write_tree`` to re-render ``tree.codoc`` and the sidecar — the
   proposal surfaces in the ``# ── pending changes`` block tagged **agent plan**.

The event id is returned so the caller (e.g. the CLI or a test) can reference it.
"""
from __future__ import annotations

from pathlib import Path

from codoc.codoc_file.render import write_tree
from codoc.loop.apply import apply_op
from codoc.model.event import PLAN_SOURCE
from codoc.model.event import NodeOp, NodeOpKind
from codoc.store.db import open_store


def propose_plan(
    root_dir: str,
    *,
    kind: str,
    title: str | None = None,
    description: str | None = None,
    parent_id: str | None = None,
    feature_id: str | None = None,
    rationale: str = "",
    binds: list[str] | None = None,
    after_id: str = "",
    before_id: str = "",
) -> str:
    """Create a plan proposal (``applied=False`` Event) and re-render the tree.

    Parameters
    ----------
    root_dir:
        Repository root — ``.codoc`` is looked up as ``<root_dir>/.codoc``.
    kind:
        One of ``"add_node"``, ``"amend"``, ``"retire_node"``, ``"move_node"``.
    title:
        Feature title (required for ``add_node`` / ``amend``).
    description:
        Feature description prose (required for ``add_node`` / ``amend``).
    parent_id:
        Parent feature id for ``add_node`` / ``move_node``.
    feature_id:
        Target feature id (required for ``amend``, ``retire_node``, ``move_node``).
    rationale:
        One-line justification shown in the pending-changes hunk.
    binds:
        Bindings as ``"file.py::symbol_path"`` strings.
    after_id / before_id:
        The siblings the node goes between, for ``add_node`` / ``move_node`` — sibling
        order stated as neighbour IDENTITIES rather than an index, so a sibling added or
        retired before the proposal is accepted cannot change where the node lands. Both
        empty means no opinion about order, which appends.

    Returns
    -------
    str
        The newly created Event id (``"e-xxxxxxxx"`` form).
    """
    codoc_dir = str(Path(root_dir) / ".codoc")

    try:
        op_kind = NodeOpKind(kind)
    except ValueError:
        valid = ", ".join(k.value for k in NodeOpKind)
        raise ValueError(f"Unknown proposal kind {kind!r}. Valid kinds: {valid}") from None

    # symbol_path is the FULL "file::qualified" form the indexer emits (see
    # codoc/lang/python.py), so the binding matches a real chunk. `file` is the
    # prefix before the first "::".
    # A bind with no "::" names a file, not a chunk; storing it as (b, b) made a
    # binding that could never match an indexed chunk, so it is dropped instead.
    parsed_binds: list[tuple[str, str]] = []
    for b in (binds or []):
        if "::" in b:
            parsed_binds.append((b.split("::", 1)[0], b))

    op = NodeOp(
        kind=op_kind,
        feature_id=feature_id,
        parent_id=parent_id,
        title=title,
        description=description,
        bindings=parsed_binds,
        rationale=rationale,
        after_id=after_id,
        before_id=before_id,
        # A plan ADD_NODE is an explicit, not-yet-built placeholder: born
        # unrealized (parity with mcp.tools.plan_add) so the lifecycle flips it
        # realized only when code first binds, and so Loop B treats accepting it
        # as a build request. A plan that already cites code stays realized.
        realized=False if (op_kind is NodeOpKind.ADD_NODE and not parsed_binds) else None,
    )

    with open_store(codoc_dir) as store:
        event = apply_op(op, store, source=PLAN_SOURCE, applied=False)
        write_tree(store, codoc_dir)

    return event.id
