"""Load prompts from prompts/ directory and parse <solution> JSON (references api.config)."""

import os
import re
import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Prompts directory: CODENAV_PROMPTS_DIR or repo root / prompts (relative to this file)
def _prompts_dir() -> Path:
    env_dir = os.environ.get("CODENAV_PROMPTS_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    # Assume repo layout: .../CodeNav/prompts, CodeNav/server/api/semantic_tree/llm/...
    current = Path(__file__).resolve().parent
    for _ in range(6):
        current = current.parent
        prompts = current / "prompts"
        if prompts.is_dir():
            return prompts
    return Path(__file__).resolve().parent.parent.parent.parent.parent / "prompts"


def load_prompt(name: str) -> str:
    """Load prompt template by name (e.g. domain_discovery, semantic_parsing, hierarchical_construction)."""
    path = _prompts_dir() / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def format_prompt(
    template: str,
    repo_name: str = "",
    repo_info: str = "",
    **kwargs: Any,
) -> str:
    """Substitute {repo_name}, {repo_info}, and any other placeholders in template."""
    return template.format(repo_name=repo_name, repo_info=repo_info, **kwargs)


_SOLUTION_RE = re.compile(r"<solution>\s*(.*?)\s*</solution>", re.DOTALL | re.IGNORECASE)


def parse_solution_block(response: str) -> Optional[str]:
    """Extract first <solution>...</solution> block content; return None if not found."""
    m = _SOLUTION_RE.search(response)
    if not m:
        return None
    return m.group(1).strip()


def parse_solution_json(response: str) -> Any:
    """
    Extract <solution> block and parse as JSON. Raises ValueError if block missing or invalid.
    """
    raw = parse_solution_block(response)
    if raw is None:
        raise ValueError("No <solution> block in LLM response")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in <solution> block: {e}") from e
