"""Step 3: Domain discovery — LLM identifies functional areas from file list and context."""

import logging
from typing import List, Optional

from api.semantic_tree.models import CodebaseSnapshot, FunctionalArea
from api.semantic_tree.llm.prompt_loader import load_prompt, format_prompt, parse_solution_json
from api.semantic_tree.llm.completion import complete

logger = logging.getLogger(__name__)


def _repo_info_from_snapshot(snapshot: CodebaseSnapshot, max_files: int = 200) -> str:
    """Build a short repo overview for the prompt."""
    lines = [f"Root: {snapshot.root_dir}", f"Files: {len(snapshot.files)}"]
    for f in snapshot.files[:max_files]:
        entity_names = [e.name for e in f.entities]
        lines.append(f"  {f.fpath}: {', '.join(entity_names[:15])}{' ...' if len(entity_names) > 15 else ''}")
    return "\n".join(lines)


def run_domain_discovery(
    snapshot: CodebaseSnapshot,
    repo_name: str = "",
    repo_info: Optional[str] = None,
    provider: str = "openai",
    model: Optional[str] = None,
) -> List[FunctionalArea]:
    """
    Call LLM to identify functional areas. Uses prompts/domain_discovery.txt.
    Raises if provider/model not configured or no <solution> in response.
    """
    if repo_info is None:
        repo_info = _repo_info_from_snapshot(snapshot)

    template = load_prompt("domain_discovery")
    prompt = format_prompt(template, repo_name=repo_name, repo_info=repo_info)
    # Append file list for context
    file_list = "\n".join(f.fpath for f in snapshot.files[:300])
    prompt += f"\n\n### File list\n{file_list}"

    response = complete(prompt=prompt, provider=provider, model=model)
    data = parse_solution_json(response)

    raw_names: List[str] = []
    if isinstance(data, list):
        raw_names = [str(x).strip() for x in data]
    elif isinstance(data, dict):
        raw_names = [str(k).strip() for k in data]
    else:
        raise ValueError(f"Unexpected domain_discovery response type: {type(data)}")

    # Normalize: lowercase, strip, collapse whitespace; dedup (keep first occurrence)
    seen: set[str] = set()
    result: List[FunctionalArea] = []
    for n in raw_names:
        normalized = " ".join(n.lower().split()).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(FunctionalArea(name=normalized))
    return result
