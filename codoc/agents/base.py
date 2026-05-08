"""
codoc.agents.base — shared utilities for all LLM agents.
"""

from __future__ import annotations

import json
import re

from codoc.config import LLMConfig, complete, get_llm_config


def load_prompt(name: str) -> str:
    """Load a prompt template from codoc/prompts/{name}.txt."""
    from pathlib import Path

    prompts_dir = Path(__file__).parent.parent / "prompts"
    return (prompts_dir / f"{name}.txt").read_text()


def format_prompt(template: str, **kwargs) -> str:
    """Substitute {variable} placeholders in *template* for each kwarg.

    Uses explicit str.replace() per key so that JSON examples containing
    literal braces (e.g. {"slug": "..."}) are left untouched.
    """
    result = template
    for key, value in kwargs.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result


def parse_solution(response: str) -> dict | list:
    """Extract and parse JSON from a response.

    Tries in order:
    1. <solution>...</solution> tags (preferred)
    2. A fenced ```json ... ``` block
    3. The first JSON array or object found anywhere in the response
    """
    # 1. Explicit solution tags
    match = re.search(r"<solution>(.*?)</solution>", response, re.DOTALL)
    if match:
        return json.loads(match.group(1).strip())

    # 2. Fenced code block
    fence = re.search(r"```(?:json)?\s*([\[{].*?)\s*```", response, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))

    # 3. Bare JSON array or object (greedy from first [ or {)
    for start_char, end_char in (("[", "]"), ("{", "}")):
        idx = response.find(start_char)
        if idx != -1:
            # Find the matching close bracket by tracking depth
            depth = 0
            in_string = False
            escape = False
            for i, ch in enumerate(response[idx:], start=idx):
                if escape:
                    escape = False
                    continue
                if ch == "\\" and in_string:
                    escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == start_char:
                    depth += 1
                elif ch == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(response[idx : i + 1])
                        except json.JSONDecodeError:
                            break

    raise ValueError(
        f"No parseable JSON found in LLM response: {response[:300]!r}"
    )


def run_agent(prompt: str, config: LLMConfig | None = None) -> dict | list:
    """Run the LLM with *prompt* and return the parsed JSON solution."""
    cfg = config or get_llm_config()
    response = complete(prompt, cfg)
    return parse_solution(response)
