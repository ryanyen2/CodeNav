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
) -> list[NodeOp]:
    """Run the single tree-update LLM call and return its ops (possibly empty).

    Prompt layout is cache-aligned (see the template's CACHE_BREAK markers):
    frozen instructions first, the whole-tree title outline second (byte-stable
    between tree mutations), and the per-call change set last — so consecutive
    passes pay cache-read prices for everything but the change itself. This is
    a structured-extraction call, so it defaults to the fast model tier.

    ``doc_language`` is the tree's authoring language (None ⇒ English). It rides
    in the frozen prefix, so switching it costs one cache miss and nothing after.
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
    raw = run_agent(volatile, config or fast_llm_config(),
                    prefix_parts=prefix_parts)
    ops_raw = raw.get("ops", []) if isinstance(raw, dict) else raw
    # Per-op tolerance (dead-letter): one malformed op (a bad/absent kind, a missing key,
    # a pydantic validation failure) must NOT sink the whole response. Before this, a
    # single bad op raised out of the comprehension, the pass errored, and the state-based
    # reconcile re-issued every subsequent save — an unbounded retry/cost loop. Now a bad
    # op is dropped with a warning and the good ops still apply.
    import logging

    ops: list[NodeOp] = []
    for o in ops_raw:
        try:
            ops.append(_coerce_op(o))
        except Exception as exc:  # noqa: BLE001 — tolerate one bad op, keep the rest
            logging.getLogger(__name__).warning(
                "codoc: dropping malformed LLM tree-update op (%s): %r", exc, o)
    return ops
