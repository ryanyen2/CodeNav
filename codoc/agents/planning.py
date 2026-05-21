"""Planning agent — reads the codoc feature tree and proposes semantic changes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal


@dataclass
class PlanOp:
    kind: Literal["introduce", "amend", "rename", "retire"]
    slug: str                    # target slug (empty string for introduce)
    title: str = ""              # new/proposed display title
    parent_slug: str = ""        # parent slug for introduce ops
    intent: str = ""             # new/proposed intent
    coding_directive: str = ""   # what code change to make
    rationale: str = ""          # why this semantic change


def plan_agent(
    prompt: str,
    feature_summaries: list[dict],
    repo_name: str = "codebase",
    config=None,
) -> list[PlanOp]:
    """Call the planning LLM and return a list of PlanOps.

    Args:
        prompt: The user's planning request.
        feature_summaries: List of {"slug": ..., "title": ..., "intent": ...} dicts.
        repo_name: Name of the repository for context.
        config: LLMConfig instance; loads from env if None.

    Returns:
        List of PlanOp instances describing proposed tree changes.
    """
    if config is None:
        from codoc.config import get_llm_config
        config = get_llm_config()

    from codoc.agents.base import load_prompt, format_prompt
    from codoc.config import complete

    prompt_template = load_prompt("planning")
    feature_text = "\n".join(
        f"- {f['slug']}: {f.get('title', f['slug'])} — {f.get('intent', '')[:100]}"
        for f in feature_summaries[:200]  # cap at 200 features for prompt size
    )
    filled = format_prompt(
        prompt_template,
        prompt=prompt,
        repo_name=repo_name,
        feature_summaries=feature_text,
    )

    response = complete(filled, config=config)
    return _parse_plan_ops(response)


def _parse_plan_ops(response: str) -> list[PlanOp]:
    """Parse LLM response into PlanOps. Expects a JSON array."""
    # Extract JSON from possible markdown code fences.
    text = response.strip()
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        inner = text[start + 3:end].strip()
        if inner.startswith("json"):
            inner = inner[4:].strip()
        text = inner

    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return []

    if not isinstance(raw, list):
        return []

    ops: list[PlanOp] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind", "")
        if kind not in ("introduce", "amend", "rename", "retire"):
            continue
        ops.append(PlanOp(
            kind=kind,
            slug=item.get("slug", ""),
            title=item.get("title", ""),
            parent_slug=item.get("parent_slug", ""),
            intent=item.get("intent", ""),
            coding_directive=item.get("coding_directive", ""),
            rationale=item.get("rationale", ""),
        ))
    return ops
