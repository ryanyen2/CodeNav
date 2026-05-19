"""codoc.agents.prose_reconciliation — reconcile prose for a structural-change obligation.

Given an Obligation of kind ``RECONCILE_PROSE`` (emitted by CascadeEnumerator),
calls the LLM to generate updated intent prose and returns a patch dict.
"""

from __future__ import annotations

import json

from codoc.agents.base import format_prompt, load_prompt, run_agent
from codoc.config import LLMConfig
from codoc.model.obligation import Obligation, ObligationKind


def reconcile_prose(
    obligation: Obligation,
    feature_summary: dict,
    neighbours: list[dict] | None = None,
    config: LLMConfig | None = None,
) -> dict:
    """Run the LLM and return the prose patch ``{"intent": str}``.

    Parameters
    ----------
    obligation:
        Must be of kind ``RECONCILE_PROSE``.
    feature_summary:
        ``{uuid, slug, intent}`` describing the affected feature.
    neighbours:
        Optional list of ``{uuid, slug, intent}`` for sibling/parent context.
    config:
        Optional LLM config; falls back to ``get_llm_config()`` when None.
    """
    if obligation.kind != ObligationKind.RECONCILE_PROSE:
        raise ValueError(
            f"reconcile_prose called with obligation kind {obligation.kind!r}; expected RECONCILE_PROSE"
        )

    template = load_prompt("prose_reconciliation")
    prompt = format_prompt(
        template,
        feature_summary=json.dumps(feature_summary, indent=2),
        change_context=json.dumps(obligation.context, indent=2),
        neighbours=json.dumps(neighbours or [], indent=2),
    )

    raw = run_agent(prompt, config)
    if not isinstance(raw, dict) or "intent" not in raw:
        raise ValueError(
            f"prose_reconciliation agent returned unexpected shape: {raw!r}"
        )
    return {"intent": str(raw["intent"]).strip()}
