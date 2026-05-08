"""
codoc.agents.attribution — proposes a reflective transaction for a changed chunk.

Phase 1, pipeline B: given a changed code chunk and its 1-hop neighbourhood
(tree-structural, binding-graph, file-locality), calls the LLM to decide
which reflective transaction (INTRODUCE, ABSORB, EVICT, …) the feature map
should receive, and what the kind-specific payload should be.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from codoc.config import LLMConfig
from codoc.agents.base import format_prompt, load_prompt, run_agent
from codoc.model.transaction import TransactionKind


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class AttributionInput:
    """All context needed to judge a single changed chunk."""

    file: str
    """Repo-relative posix path of the changed file."""

    symbol_path: str
    """Full symbol path, e.g. ``api/parser.py::RequestParser.parse``."""

    source_snippet: str
    """First ~400 chars of the chunk source at its new (or last-known) state."""

    change_kind: str
    """One of: ``"added"`` | ``"modified"`` | ``"removed"`` | ``"anchor_broken"``."""

    current_binding: dict | None
    """Serialised :class:`~codoc.model.binding.Binding` if the chunk was
    previously attributed; ``None`` otherwise."""

    neighboring_features: list[dict] = field(default_factory=list)
    """List of ``{uuid, slug, intent, binding_count}`` for each feature in the
    1-hop neighbourhood."""


@dataclass
class AttributionProposal:
    """The LLM's attribution decision for one changed chunk."""

    kind: TransactionKind
    """Which reflective transaction to emit."""

    payload: dict
    """Kind-specific payload, ready to set as ``Transaction.payload``."""

    rationale: str
    """Short explanation from the model (for logging / UI display)."""


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------


def propose_attribution(
    input: AttributionInput,
    repo_name: str = "codebase",
    config: LLMConfig | None = None,
) -> AttributionProposal:
    """Call the LLM and return an attribution proposal.

    Parameters
    ----------
    input:
        All context for the changed chunk.
    repo_name:
        Human-readable repository name (for prompt context).
    config:
        LLM configuration; falls back to ``get_llm_config()`` when *None*.

    Returns
    -------
    AttributionProposal
        Parsed proposal with ``kind``, ``payload``, and ``rationale``.

    Raises
    ------
    ValueError
        If the LLM response contains no ``<solution>`` block or the JSON is
        malformed / missing required fields.
    KeyError
        If the ``kind`` value returned by the model is not a valid
        :class:`~codoc.model.transaction.TransactionKind`.
    """
    template = load_prompt("attribution_judgment")

    chunk_description = json.dumps(
        {
            "file": input.file,
            "symbol_path": input.symbol_path,
            "source_snippet": input.source_snippet,
        },
        indent=2,
    )
    neighboring_json = json.dumps(input.neighboring_features, indent=2)
    current_binding_json = (
        json.dumps(input.current_binding, indent=2)
        if input.current_binding is not None
        else "null"
    )

    prompt = format_prompt(
        template,
        repo_name=repo_name,
        chunk_description=chunk_description,
        change_summary=input.change_kind,
        neighboring_features=neighboring_json,
        current_binding=current_binding_json,
    )

    raw: dict = run_agent(prompt, config)  # type: ignore[assignment]

    kind_str: str = raw["kind"]
    kind = TransactionKind(kind_str)

    # The rationale field is present in every payload shape; extract it here
    # so that AttributionProposal.rationale is always populated, then keep the
    # full raw dict as the payload (handlers downstream use the whole object).
    rationale: str = raw.get("rationale", "")

    return AttributionProposal(
        kind=kind,
        payload=raw,
        rationale=rationale,
    )
