"""Tree patch: LLM produces structured patch (insert/update/delete); applier merges into prior markdown."""

import re
import logging
from typing import Any, Dict, List, Optional, Tuple

from api.semantic_tree.llm.prompt_loader import load_prompt, parse_solution_json
from api.semantic_tree.llm.completion import complete

logger = logging.getLogger(__name__)

_FPATH_RE = re.compile(r"\[([^\]]+)\]")
_ENTITY_RE = re.compile(r"\(([^)]+)\)")


def _parse_tree_lines(md: str) -> Tuple[List[Tuple[int, str, Optional[str], Optional[str]]], int]:
    """
    Parse tree markdown into (depth, line, fpath, entity_name) per line. Stops at deps:.
    Returns (list of (depth, raw_line, fpath, entity_name), deps_start_line or -1).
    """
    lines = md.splitlines()
    result: List[Tuple[int, str, Optional[str], Optional[str]]] = []
    deps_start = -1
    current_fpath: Optional[str] = None
    for i, line in enumerate(lines):
        if line.strip() == "deps:":
            deps_start = i
            break
        if not line.strip():
            continue
        depth = (len(line) - len(line.lstrip())) // 2
        fpath_m = _FPATH_RE.search(line)
        entity_m = _ENTITY_RE.search(line)
        fpath = fpath_m.group(1) if fpath_m else None
        entity_name = entity_m.group(1) if entity_m else None
        if fpath:
            current_fpath = fpath
        if current_fpath and (fpath or entity_name):
            result.append((depth, line, current_fpath, entity_name))
        else:
            result.append((depth, line, None, None))
    return result, deps_start


def _node_ranges(parsed: List[Tuple[int, str, Optional[str], Optional[str]]]) -> Dict[str, Tuple[int, int]]:
    """Map entity_id (fpath::name) or fpath (for file nodes) to (start_line_0based, end_line_0based)."""
    out: Dict[str, Tuple[int, int]] = {}
    for i, (depth, _line, fpath, entity_name) in enumerate(parsed):
        if fpath and entity_name:
            eid = f"{fpath}::{entity_name}"
        elif fpath:
            eid = fpath
        else:
            continue
        start = i
        end = i
        for j in range(i + 1, len(parsed)):
            if parsed[j][0] <= depth:
                break
            end = j
        out[eid] = (start, end)
    return out


def apply_patch_to_markdown(prior_md: str, patch: Dict[str, Any]) -> str:
    """
    Apply structured patch to prior tree markdown. Patch has insertions, updates, deletions.
    Returns merged markdown.
    """
    lines = prior_md.splitlines()
    parsed, deps_start = _parse_tree_lines(prior_md)
    if deps_start < 0:
        deps_start = len(lines)
    tree_lines = lines[:deps_start]
    rest = lines[deps_start:]
    ranges = _node_ranges(parsed)

    # Build logical line list (only tree part; indices match parsed)
    tree_line_list = [p[1] for p in parsed]
    if not tree_line_list:
        tree_line_list = [l for l in tree_lines if l.strip()]

    insertions = patch.get("insertions") or []
    updates = patch.get("updates") or []
    deletions = patch.get("deletions") or []

    # Apply deletions (from end to start so indices don't shift)
    to_drop: set = set()
    for eid in deletions:
        if eid in ranges:
            s, e = ranges[eid]
            for k in range(s, e + 1):
                to_drop.add(k)
    new_list: List[str] = []
    for i, ln in enumerate(tree_line_list):
        if i not in to_drop:
            new_list.append(ln)
    tree_line_list = new_list
    # Recompute ranges after deletion (we'd need to re-parse; for simplicity re-parse from new_list)
    reparsed, _ = _parse_tree_lines("\n".join(tree_line_list))
    ranges = _node_ranges(reparsed)

    # Apply updates (replace feature on the line for entity_id)
    for upd in updates:
        eid = upd.get("entity_id")
        feature = upd.get("feature")
        if not eid or feature is None or eid not in ranges:
            continue
        s, e = ranges[eid]
        if s < len(tree_line_list):
            line = tree_line_list[s]
            # Replace feature part: after sigil+space, before [ or ( or #
            m = re.match(r"^(\s*-\s*[%~$^]\s+)(.+?)(?:\s+\[|\s+\(|\s+#|$)", line)
            if m:
                tree_line_list[s] = m.group(1) + feature + line[m.end(2):]
    ranges = _node_ranges(_parse_tree_lines("\n".join(tree_line_list))[0])

    # Apply insertions (after_entity_id -> insert new_lines)
    for ins in insertions:
        after_id = ins.get("after_entity_id")
        new_lines = ins.get("new_lines") or []
        if not after_id or after_id not in ranges:
            continue
        _s, e = ranges[after_id]
        insert_at = e + 1
        for nl in reversed(new_lines):
            tree_line_list.insert(insert_at, nl)

    return "\n".join(tree_line_list) + ("\n" + "\n".join(rest) if rest else "")


def run_tree_patch_llm(
    prior_tree_md: str,
    code_delta_description: str,
    provider: str = "openai",
    model: Optional[str] = None,
    max_tree_chars: int = 12000,
) -> Dict[str, Any]:
    """
    Call LLM to produce a structured patch (insertions, updates, deletions) given current tree and code delta.
    """
    template = load_prompt("tree_patch")
    current_excerpt = prior_tree_md[:max_tree_chars]
    if len(prior_tree_md) > max_tree_chars:
        current_excerpt += "\n... (truncated)"
    prompt = template.format(current_tree=current_excerpt, code_delta=code_delta_description)
    response = complete(prompt=prompt, provider=provider, model=model)
    data = parse_solution_json(response)
    if not isinstance(data, dict):
        return {"insertions": [], "updates": [], "deletions": []}
    return {
        "insertions": data.get("insertions", []),
        "updates": data.get("updates", []),
        "deletions": data.get("deletions", []),
    }
