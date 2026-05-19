"""codoc.agents.binding_reconciliation — reconcile a displaced binding.

Given an Obligation of kind ``RECONCILE_BINDING`` (emitted by CascadeEnumerator),
calls the LLM to decide which feature a displaced binding should be attributed
to and returns a patch dict.
"""

from __future__ import annotations

import json

from codoc.agents.base import format_prompt, load_prompt, run_agent
from codoc.config import LLMConfig
from codoc.model.obligation import Obligation, ObligationKind


def reconcile_binding(
    obligation: Obligation,
    binding_summary: dict,
    candidate_features: list[dict],
    config: LLMConfig | None = None,
) -> dict:
    """Run the LLM and return the binding patch ``{"feature_uuid": str}``.

    Parameters
    ----------
    obligation:
        Must be of kind ``RECONCILE_BINDING``.
    binding_summary:
        Dict describing the displaced binding (uuid, anchor, fingerprint, prior
        feature).
    candidate_features:
        List of ``{uuid, slug, intent}`` dicts for candidate target features.
    config:
        Optional LLM config; falls back to ``get_llm_config()`` when None.
    """
    if obligation.kind != ObligationKind.RECONCILE_BINDING:
        raise ValueError(
            f"reconcile_binding called with obligation kind {obligation.kind!r}; expected RECONCILE_BINDING"
        )

    template = load_prompt("binding_reconciliation")
    prompt = format_prompt(
        template,
        binding_summary=json.dumps(binding_summary, indent=2),
        candidate_features=json.dumps(candidate_features, indent=2),
        change_context=json.dumps(obligation.context, indent=2),
    )

    raw = run_agent(prompt, config)
    if not isinstance(raw, dict) or "feature_uuid" not in raw:
        raise ValueError(
            f"binding_reconciliation agent returned unexpected shape: {raw!r}"
        )
    return {
        "feature_uuid": str(raw["feature_uuid"]),
        "rationale": str(raw.get("rationale", "")),
    }
