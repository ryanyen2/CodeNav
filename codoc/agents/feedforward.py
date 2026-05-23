"""Feedforward agent: fills missing codoc fields before code is generated.

Given a placeholder/partial feature, asks the LLM to complete purpose/rationale/scenario
and produce a coding_directive describing where and how to implement the feature.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FeedforwardResult:
    purpose: str = ""
    rationale: str = ""
    scenario: str = ""
    coding_directive: str = ""
    target_files: list[str] = field(default_factory=list)
    error: str = ""


def run_feedforward_agent(
    title: str,
    partial_description: str,
    existing_features: list[dict],
    repo_name: str = "",
    config=None,
) -> FeedforwardResult:
    """Call the LLM to fill missing fields for a placeholder feature."""
    from codoc.agents.base import load_prompt, format_prompt, parse_solution
    from codoc.config import get_llm_config, complete

    cfg = config or get_llm_config()
    template = load_prompt("feedforward")

    existing_str = "\n".join(
        f"- {f['slug']}: {f.get('purpose') or f.get('intent', '')}"
        for f in existing_features[:20]
    )

    prompt = format_prompt(
        template,
        repo_name=repo_name,
        title=title,
        partial_description=partial_description or "(none provided)",
        existing_features=existing_str or "(none)",
    )

    try:
        response = complete(prompt, cfg)
        data = parse_solution(response)
        if not isinstance(data, dict):
            return FeedforwardResult(error="LLM returned non-dict response")

        return FeedforwardResult(
            purpose=data.get("purpose", ""),
            rationale=data.get("rationale", ""),
            scenario=data.get("scenario", ""),
            coding_directive=data.get("coding_directive", ""),
            target_files=data.get("target_files", []),
        )
    except Exception as exc:
        return FeedforwardResult(error=str(exc))
