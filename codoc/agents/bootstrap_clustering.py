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
    """1-2 sentences summary of the feature's behavioral purpose."""

    title: str = ""
    """3-6 word NL display name (sentence case), e.g. 'Request body parsing'."""

    description: str = ""
    """2-6 sentence multi-paragraph prose: what this does, why it exists, how it relates to siblings."""

    provisional_uuid: str = ""
    """Stable UUID minted at proposal time so children can reference parent before accept."""

    parent_title_hint: str = ""
    """Parent feature title hint for hierarchical context (empty = top-level)."""

    candidate_chunk_keys: list[str] = field(default_factory=list)
    """symbol_paths of chunks that should be bound to this feature."""


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------


def _new_provisional_uuid() -> str:
    try:
        import uuid_utils  # type: ignore[import]
        return str(uuid_utils.uuid7())
    except ImportError:
        import uuid
        return str(uuid.uuid4())


def propose_features_for_cluster(
    cluster: ClusterInput,
    existing_feature_summaries: list[dict],
    repo_name: str = "codebase",
    config: LLMConfig | None = None,
) -> list[FeatureProposal]:
    """Call the LLM for one cluster and return the proposed features."""
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
        parent_feature="",
        sibling_features="[]",
        depth=0,
    )

    raw: list[dict] = run_agent(prompt, config)  # type: ignore[assignment]

    proposals: list[FeatureProposal] = []
    for item in raw:
        proposals.append(
            FeatureProposal(
                slug=item["slug"],
                intent=item.get("intent", ""),
                title=item.get("title", ""),
                description=item.get("description", ""),
                provisional_uuid=_new_provisional_uuid(),
                parent_title_hint=item.get("parent_title_hint", ""),
                candidate_chunk_keys=item.get("candidate_chunk_keys", []),
            )
        )
    return proposals


def propose_subtree(
    cluster: ClusterInput,
    parent_feature_title: str,
    parent_feature_intent: str,
    sibling_titles: list[str],
    existing_feature_summaries: list[dict],
    depth: int,
    repo_name: str = "codebase",
    config: LLMConfig | None = None,
) -> list[FeatureProposal]:
    """Call the LLM for one cluster with parent+sibling context.

    Used by the recursive bootstrap to propose features at a specific tree depth
    with awareness of where they sit in the overall hierarchy.

    Parameters
    ----------
    cluster:
        The cluster to analyse at this level.
    parent_feature_title:
        Title of the parent feature (or ``"<repo-root>"`` for top-level clusters).
    parent_feature_intent:
        Intent of the parent feature (or empty for the root).
    sibling_titles:
        Titles of features already proposed at this same level (for dedup).
    existing_feature_summaries:
        All features already in the whole tree (global dedup guard).
    depth:
        Current tree depth (0 = top level, 1 = second level, …).
    repo_name:
        Human-readable repository name.
    config:
        LLM configuration; falls back to default when *None*.
    """
    template = load_prompt("bootstrap_clustering")

    parent_ctx = (
        f"{parent_feature_title}: {parent_feature_intent}"
        if parent_feature_intent
        else parent_feature_title
    )
    clusters_json = json.dumps(cluster.chunks, indent=2)
    existing_json = (
        json.dumps(existing_feature_summaries, indent=2)
        if existing_feature_summaries
        else "[]"
    )
    sibling_json = json.dumps(sibling_titles, indent=2)

    prompt = format_prompt(
        template,
        repo_name=repo_name,
        clusters=clusters_json,
        existing_features=existing_json,
        parent_feature=parent_ctx,
        sibling_features=sibling_json,
        depth=depth,
    )

    raw: list[dict] = run_agent(prompt, config)  # type: ignore[assignment]

    proposals: list[FeatureProposal] = []
    for item in raw:
        proposals.append(
            FeatureProposal(
                slug=item["slug"],
                intent=item.get("intent", ""),
                title=item.get("title", ""),
                description=item.get("description", ""),
                provisional_uuid=_new_provisional_uuid(),
                parent_title_hint=parent_feature_title,
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
