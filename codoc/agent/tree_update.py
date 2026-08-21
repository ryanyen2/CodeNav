"""The single LLM tree-update pass.

One call sees the whole change set + the relevant subtree + every node title and
returns the minimal list of :class:`NodeOp`. Because it sees all candidates and
all titles at once, it can fold sibling chunks into one node instead of emitting
duplicate introduces — there is no per-chunk loop and nothing to dedup afterward.
"""
from __future__ import annotations

import json

from codoc.agent.base import (
    format_prompt,
    load_prompt,
    run_agent,
    split_prompt,
    titles_outline,
)
from codoc.config import LLMConfig, fast_llm_config
from codoc.doclang import DocLanguage
from codoc.loop import prose
from codoc.model.event import NodeOp, NodeOpKind


def _coerce_op(raw: dict) -> NodeOp:
    bindings = [tuple(b) for b in raw.get("bindings", []) if len(b) == 2]
    return NodeOp(
        kind=NodeOpKind(raw["kind"]),
        feature_id=raw.get("feature_id"),
        parent_id=raw.get("parent_id"),
        title=raw.get("title"),
        description=raw.get("description"),
        bindings=bindings,
        rationale=raw.get("rationale", ""),
    )


def propose_tree_update(
    changes: dict,
    subtree: list[dict],
    all_titles: list[dict],
    *,
    repo_name: str = "codebase",
    config: LLMConfig | None = None,
    doc_language: DocLanguage | None = None,
    check_prose: bool = True,
) -> list[NodeOp]:
    """Run the single tree-update LLM call and return its ops (possibly empty).

    Prompt layout is cache-aligned (see the template's CACHE_BREAK markers):
    frozen instructions first, the whole-tree title outline second (byte-stable
    between tree mutations), and the per-call change set last — so consecutive
    passes pay cache-read prices for everything but the change itself. This is
    a structured-extraction call, so it defaults to the fast model tier.

    ``doc_language`` is the tree's authoring language (None ⇒ English). It rides
    in the frozen prefix, so switching it costs one cache miss and nothing after.

    ``check_prose`` runs the answer past :mod:`codoc.loop.prose` and, if a
    description broke a rule the style guide states, asks once more with the
    defects named. Defaulting to ON is deliberate, against the convention that an
    optional model call defaults off: :func:`codoc.loop.loop_a.apply_changeset`
    calls this through an injected ``propose`` with a fixed signature, so a flag
    only production passes is a flag production cannot pass, and a gate that has
    to be remembered is a gate that is off. The cost is bounded by the defect
    rate, since a clean answer never triggers a second call.
    """
    # Split the raw template FIRST, then substitute per segment — substituted
    # values are repo-derived and may contain a literal marker.
    prefix_tpls, volatile_tpl = split_prompt(
        load_prompt("tree_update", doc_language=doc_language))
    kwargs = dict(
        repo_name=repo_name,
        changes=json.dumps(changes, indent=2, sort_keys=True, ensure_ascii=False),
        subtree=json.dumps(subtree, indent=2, sort_keys=True, ensure_ascii=False),
        all_titles=titles_outline(all_titles),
    )
    prefix_parts = [format_prompt(t, **kwargs) for t in prefix_tpls]
    volatile = format_prompt(volatile_tpl, **kwargs)
    import logging

    # Whole-response tolerance, the outer half of the per-op tolerance below.
    # Per-op dropping only helps once the response has parsed; a reply that is
    # not valid JSON at all raises out of `parse_solution` and takes the entire
    # Loop A pass with it — including the deterministic refresh/relocate/detach
    # work that had already succeeded, which then gets re-derived and re-issued
    # on the next state-based reconcile. One truncated reply on a 158-commit
    # altair replay did exactly that (`Expecting ',' delimiter` at char 3751 of
    # a large `altair.datasets` changeset).
    #
    # Returning no ops is the right degradation: the safe ops stand, the added
    # chunks stay unbound, and the next pass sees them as still-unattributed and
    # asks again. A crash loses both.
    def _ask(extra: str = "") -> list[NodeOp]:
        """One attempt. ``extra`` is appended to the VOLATILE tail, never the prefix.

        A repair pass is this same call with the critique on the end, which is why
        there is no second prompt file: the rules are already in the frozen prefix
        and the reply is what changed. Appending to the tail also keeps the cache
        prefix byte-identical, so the retry pays for the critique and nothing else.
        """
        try:
            raw = run_agent(volatile + extra, config or fast_llm_config(),
                            prefix_parts=prefix_parts)
        except Exception as exc:  # noqa: BLE001 — a bad reply must not sink the pass
            logging.getLogger(__name__).warning(
                "codoc: unparseable LLM tree-update response (%s); no ops this pass",
                exc)
            return []
        ops_raw = raw.get("ops", []) if isinstance(raw, dict) else raw
        # Per-op tolerance (dead-letter): one malformed op (a bad/absent kind, a missing
        # key, a pydantic validation failure) must NOT sink the whole response. Before
        # this, a single bad op raised out of the comprehension, the pass errored, and the
        # state-based reconcile re-issued every subsequent save — an unbounded retry/cost
        # loop. Now a bad op is dropped with a warning and the good ops still apply.
        out: list[NodeOp] = []
        for o in ops_raw:
            try:
                out.append(_coerce_op(o))
            except Exception as exc:  # noqa: BLE001 — tolerate one bad op, keep the rest
                logging.getLogger(__name__).warning(
                    "codoc: dropping malformed LLM tree-update op (%s): %r", exc, o)
        return out

    ops = _ask()
    if not ops or not check_prose:
        return ops
    names_of, depth_of, children_of = _node_context(subtree, all_titles)
    kept, _findings = prose.gate(
        ops, rerun=_ask, names_of=names_of, depth_of=depth_of,
        children_of=children_of, doc_language=doc_language)
    return kept


def _node_context(subtree: list[dict], all_titles: list[dict]):
    """The three signals the prose gate needs, read off the prompt's own context.

    Nothing is fetched: an AMEND names a feature whose bindings, parent and
    children are all already in the payload this call was built from, and the gate
    is only as good as its picture of what the node covers. Without the bindings a
    rewrite of an existing description is checked as though the node had no code
    under it, which silences exactly the rules that ask whether the prose says
    anything the identifiers did not.
    """
    # A subtree binding is a symbol path (`file.py::sym`), and the gate's file-span
    # signal wants the file beside it. Splitting here rather than teaching the gate
    # about symbol-path syntax: the shape belongs to this payload, not to the critic.
    binds = {
        e["id"]: [f"{str(b).split('::')[0]} {b}" for b in (e.get("bindings") or [])]
        for e in subtree if e.get("id")
    }
    # The subtree carries each node's own altitude (loop/subtree.py computes it over
    # the whole feature list). Preferred over anything derived here, because the
    # payload is a WINDOW: a node's parent chain may reach above what was sent, and
    # a depth counted inside the window is short by however much was cut.
    stated = {e["id"]: e for e in subtree if e.get("id")}
    parents: dict[str, str | None] = {}
    for row in list(all_titles) + list(subtree):
        if row.get("id"):
            parents[row["id"]] = row.get("parent_id")
    has_kids = {pid for pid in parents.values() if pid}

    def depth_of_id(fid: str | None) -> int:
        row = stated.get(fid or "")
        if row is not None and isinstance(row.get("depth"), int):
            return row["depth"]
        seen, cur, depth = set(), parents.get(fid or ""), 0
        while cur and cur not in seen:
            seen.add(cur)
            depth += 1
            cur = parents.get(cur)
        return depth

    def names_of(op: NodeOp) -> list[str]:
        return binds.get(op.feature_id or "", [])

    def depth_of(op: NodeOp) -> int:
        if op.kind is NodeOpKind.ADD_NODE or op.parent_id:
            return depth_of_id(op.parent_id) + 1 if op.parent_id else 0
        return depth_of_id(op.feature_id)

    def children_of(op: NodeOp) -> bool | None:
        if op.kind is NodeOpKind.ADD_NODE:
            return False       # a node minted this pass has nothing under it yet
        row = stated.get(op.feature_id or "")
        if row is not None and isinstance(row.get("children"), int):
            return row["children"] > 0
        if op.feature_id in parents:
            return op.feature_id in has_kids
        return None            # not in this payload; say so rather than guess

    return names_of, depth_of, children_of
