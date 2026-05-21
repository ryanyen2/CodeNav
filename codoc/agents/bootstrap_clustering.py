"""
codoc.agents.bootstrap_clustering — proposes feature groupings from code clusters.

Phase 1, pipeline A: given a cluster of semantically-related code chunks produced
by the embedding-based grouping step, asks the LLM to name and describe one or
more features and to assign each chunk to exactly one feature.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from codoc.config import LLMConfig
from codoc.agents.base import format_prompt, load_prompt, run_agent


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class ClusterInput:
    """A single embedding-derived cluster of code chunks."""

    chunks: list[dict]
    """Each entry: {symbol_path, file, source_snippet} where source_snippet
    is the first ~300 chars of the chunk source."""

    cluster_id: int


@dataclass
class FeatureProposal:
    """A single feature proposed by the LLM for one cluster."""

    slug: str
    """Kebab-case verb-object identifier, e.g. 'parse-request-body'."""

    intent: str
    """1-2 sentences describing the feature's behavioral purpose."""

    title: str = ""
    """2-5 word prose display name, e.g. 'Request Body Parsing'."""

    parent_title_hint: str = ""
    """Chapter title hint for hierarchical clustering (empty = top-level)."""

    candidate_chunk_keys: list[str] = field(default_factory=list)
    """symbol_paths of chunks that should be bound to this feature."""


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------


def propose_features_for_cluster(
    cluster: ClusterInput,
    existing_feature_summaries: list[dict],
    repo_name: str = "codebase",
    config: LLMConfig | None = None,
) -> list[FeatureProposal]:
    """Call the LLM for one cluster and return the proposed features.

    Parameters
    ----------
    cluster:
        The cluster to analyse.
    existing_feature_summaries:
        List of ``{slug, intent}`` dicts for features already in the tree.
        Pass an empty list at cold start.
    repo_name:
        Human-readable name of the repository (for prompt context).
    config:
        LLM configuration; falls back to ``get_llm_config()`` when *None*.

    Returns
    -------
    list[FeatureProposal]
        One or more feature proposals covering all chunks in the cluster.
    """
    template = load_prompt("bootstrap_clustering")

    clusters_json = json.dumps(cluster.chunks, indent=2)
    existing_json = (
        json.dumps(existing_feature_summaries, indent=2)
        if existing_feature_summaries
        else "[]"
    )

    prompt = format_prompt(
        template,
        repo_name=repo_name,
        clusters=clusters_json,
        existing_features=existing_json,
    )

    raw: list[dict] = run_agent(prompt, config)  # type: ignore[assignment]

    proposals: list[FeatureProposal] = []
    for item in raw:
        proposals.append(
            FeatureProposal(
                slug=item["slug"],
                intent=item["intent"],
                title=item.get("title", ""),
                parent_title_hint=item.get("parent_title_hint", ""),
                candidate_chunk_keys=item.get("candidate_chunk_keys", []),
            )
        )
    return proposals


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------


def build_introduce_payload(
    proposal: FeatureProposal,
    chunk_anchors: dict[str, dict],
) -> dict:
    """Build the payload dict for an INTRODUCE transaction.

    Parameters
    ----------
    proposal:
        The ``FeatureProposal`` returned by :func:`propose_features_for_cluster`.
    chunk_anchors:
        Mapping of ``symbol_path → {file, symbol_path}`` for each candidate
        chunk key in the proposal.  The caller resolves these from the index.

    Returns
    -------
    dict
        Ready to pass as ``Transaction.payload`` for a ``INTRODUCE`` transaction.
    """
    anchors = [
        chunk_anchors[key]
        for key in proposal.candidate_chunk_keys
        if key in chunk_anchors
    ]
    return {
        "slug": proposal.slug,
        "intent": proposal.intent,
        "anchors": anchors,
    }
