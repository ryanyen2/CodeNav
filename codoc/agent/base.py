"""codoc.agent.base — shared utilities for LLM agents.

Moved verbatim from the old ``codoc.agents.base`` (the only agent helper the
rewrite keeps). ``load_prompt`` resolves ``codoc/prompts/{name}.txt`` and expands
``{{include:X}}`` directives; ``format_prompt`` does brace-safe substitution;
``parse_solution`` extracts JSON from ``<solution>`` tags / fences / bare JSON;
``run_agent`` calls the configured LLM and returns the parsed solution.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from codoc.config import LLMConfig, complete, get_llm_config


def load_prompt(name: str) -> str:
    prompts_dir = Path(__file__).parent.parent / "prompts"
    text = (prompts_dir / f"{name}.txt").read_text()

    def _expand(m: re.Match) -> str:
        inc_name = m.group(1).strip()
        inc_path = prompts_dir / f"{inc_name}.txt"
        try:
            return inc_path.read_text()
        except FileNotFoundError:
            return f"[missing include: {inc_name}]"

    return re.sub(r"\{\{include:(.*?)\}\}", _expand, text)


def format_prompt(template: str, **kwargs) -> str:
    """Substitute {variable} placeholders, leaving literal JSON braces intact."""
    result = template
    for key, value in kwargs.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result


def parse_solution(response: str) -> dict | list:
    match = re.search(r"<solution>(.*?)</solution>", response, re.DOTALL)
    if match:
        return json.loads(match.group(1).strip())

    fence = re.search(r"```(?:json)?\s*([\[{].*?)\s*```", response, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))

    for start_char, end_char in (("[", "]"), ("{", "}")):
        idx = response.find(start_char)
        if idx != -1:
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

    raise ValueError(f"No parseable JSON found in LLM response: {response[:300]!r}")


def run_agent(prompt: str, config: LLMConfig | None = None) -> dict | list:
    cfg = config or get_llm_config()
    response = complete(prompt, cfg)
    return parse_solution(response)
