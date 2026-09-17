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


def propose_tree_update_raw(
    changes: dict,
    subtree: list[dict],
    all_titles: list[dict],
    *,
    repo_name: str = "codebase",
    config: LLMConfig | None = None,
    doc_language: DocLanguage | None = None,
) -> list[dict]:
    """The v2 entry point: the model's ops as plain dicts in the prompt's JSON
    shape (``kind``, ``feature_id``, ``blocks``…). The reflect loop interprets
    them; nothing here coerces to the v1 ``NodeOp``."""
    prefix_tpls, volatile_tpl = split_prompt(load_prompt("tree_update", doc_language=doc_language))
    kwargs = dict(
        repo_name=repo_name,
        changes=json.dumps(changes, indent=2, sort_keys=True, ensure_ascii=False),
        subtree=json.dumps(subtree, indent=2, sort_keys=True, ensure_ascii=False),
        all_titles=titles_outline(all_titles),
    )
    prefix_parts = [format_prompt(t, **kwargs) for t in prefix_tpls]
    volatile = format_prompt(volatile_tpl, **kwargs)
    import logging
    try:
        raw = run_agent(volatile, config or fast_llm_config(), prefix_parts=prefix_parts)
    except Exception as exc:  # noqa: BLE001 — a bad reply must not sink the pass
        logging.getLogger(__name__).warning("codoc: unparseable tree-update response (%s); no ops", exc)
        return []
    ops = raw.get("ops", []) if isinstance(raw, dict) else raw
    return [o for o in (ops or []) if isinstance(o, dict) and isinstance(o.get("kind"), str)]
