"""
LLM-based code generation for inverse sync: map tree edit operations to concrete file edits.

Each operation type (AddNode, DeleteNode, EditFeature, etc.) is dispatched to produce
a list of CodeChange edits. AddNode and EditFeature use the LLM; DeleteNode is deterministic.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from api.semantic_tree.models import CodebaseSnapshot
from api.semantic_tree.pipeline.code_applicator import CodeChange
from api.semantic_tree.llm.completion import complete

logger = logging.getLogger(__name__)


def _file_content(root_dir: str, fpath: str) -> str:
    path = Path(root_dir) / fpath
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _targets_list(op: Dict[str, Any]) -> List[Dict[str, Any]]:
    t = op.get("targets") or []
    return t if isinstance(t, list) else [t]


def _strip_code_fence(raw: str) -> str:
    """Remove markdown code fence (```) from LLM output if present."""
    code = raw.strip()
    if not code.startswith("```"):
        return code.rstrip() + ("\n" if code and not code.endswith("\n") else "")
    lines = code.split("\n")
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    code = "\n".join(lines).rstrip()
    return code + ("\n" if code and not code.endswith("\n") else "")


def dispatch_add_node(
    op: Dict[str, Any],
    snapshot: CodebaseSnapshot,
    root_dir: str,
    provider: str,
    model: Optional[str],
) -> List[CodeChange]:
    """
    Generate new code for an added tree node (new function/feature).
    Inserts at end of file or after last line of target file.
    """
    targets = _targets_list(op)
    params = op.get("params") or {}
    feature = params.get("feature") or "implement the described behavior"
    contract = params.get("contract") or {}
    sig = (contract.get("sig") or "").strip() if isinstance(contract, dict) else ""

    # Resolve target file: from first target with fpath, or first file in snapshot
    fpath: Optional[str] = None
    insert_after_line: Optional[int] = None
    if targets:
        t = targets[0]
        if isinstance(t, dict) and t.get("fpath"):
            fpath = t["fpath"]
            lr = t.get("line_range")
            if lr and len(lr) >= 2:
                insert_after_line = int(lr[1])
    if not fpath and snapshot.files:
        fpath = snapshot.files[0].fpath
    if not fpath:
        logger.warning("AddNode: no target file; skip")
        return []

    content = _file_content(root_dir, fpath)
    lines = content.splitlines()
    if insert_after_line is None or insert_after_line < 1:
        insert_after_line = len(lines) + 1

    prompt = f"""Generate a single Python function that implements this semantic feature:

Feature: {feature}
"""
    if sig:
        prompt += f"Signature (prefer this): {sig}\n"
    prompt += """
Rules:
- Output only the function definition and body (no explanation, no markdown).
- Use a clear docstring that reflects the feature.
- Preserve the requested signature if given.
"""
    try:
        raw = complete(prompt, provider=provider, model=model)
        code = _strip_code_fence(raw)
    except Exception as e:
        logger.exception("AddNode LLM failed: %s", e)
        return []

    return [
        CodeChange(
            fpath=fpath,
            line_start=insert_after_line + 1,
            line_end=None,
            new_content="\n" + code,
        )
    ]


def dispatch_delete_node(
    op: Dict[str, Any],
    _snapshot: CodebaseSnapshot,
    root_dir: str,
) -> List[CodeChange]:
    """Remove code for deleted tree node (delete line range per target)."""
    changes: List[CodeChange] = []
    for t in _targets_list(op):
        if not isinstance(t, dict):
            continue
        fpath = t.get("fpath")
        lr = t.get("line_range")
        if not fpath or not lr or len(lr) < 2:
            continue
        start, end = int(lr[0]), int(lr[1])
        if start < 1 or end < start:
            continue
        changes.append(
            CodeChange(fpath=fpath, line_start=start, line_end=end, new_content="")
        )
    return changes


def dispatch_edit_feature(
    op: Dict[str, Any],
    snapshot: CodebaseSnapshot,
    root_dir: str,
    provider: str,
    model: Optional[str],
) -> List[CodeChange]:
    """
    Modify an existing function to match a new semantic feature (LLM rewrites the implementation).
    """
    targets = _targets_list(op)
    if not targets:
        return []
    t = targets[0]
    if not isinstance(t, dict):
        return []
    fpath = t.get("fpath")
    entity_name = t.get("entity_name")
    lr = t.get("line_range")
    if not fpath or not lr or len(lr) < 2:
        return []
    params = op.get("params") or {}
    new_feature = params.get("new_feature") or ""

    content = _file_content(root_dir, fpath)
    lines = content.splitlines()
    start, end = int(lr[0]), int(lr[1])
    if start < 1 or end > len(lines):
        return []
    current_code = "\n".join(lines[start - 1 : end])

    prompt = f"""Modify this Python function so its behavior matches this new semantic feature.
Keep the same function name and preserve the signature unless the feature requires a change.

New feature: {new_feature}

Current function (lines {start}-{end}):

```
{current_code}
```

Output only the modified function (no explanation, no markdown)."""
    try:
        raw = complete(prompt, provider=provider, model=model)
        code = _strip_code_fence(raw)
    except Exception as e:
        logger.exception("EditFeature LLM failed: %s", e)
        return []

    return [
        CodeChange(fpath=fpath, line_start=start, line_end=end, new_content=code)
    ]


def dispatch_operation_to_changes(
    op: Dict[str, Any],
    snapshot: CodebaseSnapshot,
    root_dir: str,
    provider: str = "openai",
    model: Optional[str] = None,
) -> List[CodeChange]:
    """
    Map one tree-edit operation (from TS tree_edit_targets) to a list of CodeChanges.
    Supports AddNode, DeleteNode, EditFeature. Others return empty list (no code gen yet).
    """
    op_type = (op.get("op") or "").strip()
    if op_type == "AddNode":
        return dispatch_add_node(op, snapshot, root_dir, provider, model)
    if op_type == "DeleteNode":
        return dispatch_delete_node(op, snapshot, root_dir)
    if op_type == "EditFeature":
        return dispatch_edit_feature(op, snapshot, root_dir, provider, model)
    # MoveNode, EditContract, ReorderChildren, etc.: not implemented yet
    logger.info("Code dispatch: unsupported operation %s; skip", op_type)
    return []
