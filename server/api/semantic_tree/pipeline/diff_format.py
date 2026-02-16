"""
Unified diff and search-replace block format for code changes (Phase 2).
Used by code_dispatch and API responses; enables robust apply and diff view.
"""

import difflib
import re
from pathlib import Path
from typing import List, Optional

from api.semantic_tree.pipeline.code_applicator import CodeChange


def code_changes_to_unified_diff(
    fpath: str,
    old_content: str,
    new_content: str,
    context_lines: int = 3,
) -> str:
    """
    Produce unified diff (--- a/fpath, +++ b/fpath, @@ ...) using difflib.
    """
    a_lines = old_content.splitlines(keepends=True)
    b_lines = new_content.splitlines(keepends=True)
    if not a_lines and not b_lines:
        return ""
    diff = difflib.unified_diff(
        a_lines,
        b_lines,
        fromfile=f"a/{fpath}",
        tofile=f"b/{fpath}",
        lineterm="",
        n=context_lines,
    )
    return "".join(diff)


class SearchReplaceBlock:
    """One SEARCH/REPLACE block for LLM output or API representation."""

    def __init__(self, search: str, replace: str):
        self.search = search
        self.replace = replace

    def __repr__(self) -> str:
        return f"SearchReplaceBlock(search={len(self.search)} chars, replace={len(self.replace)} chars)"


_SEARCH_REPLACE_RE = re.compile(
    r"<<<<<<<\s*SEARCH\s*\n(.*?)\n=======\s*\n(.*?)\n>>>>>>>\s*REPLACE",
    re.DOTALL,
)


def code_changes_to_search_replace(
    fpath: str,
    old_content: str,
    new_content: str,
) -> List[SearchReplaceBlock]:
    """
    Use SequenceMatcher to produce minimal search/replace blocks (SEARCH/REPLACE delimiters).
    """
    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    blocks: List[SearchReplaceBlock] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        search = "\n".join(old_lines[i1:i2]) if i2 > i1 else ""
        replace = "\n".join(new_lines[j1:j2]) if j2 > j1 else ""
        blocks.append(SearchReplaceBlock(search=search, replace=replace))
    return blocks


def apply_search_replace(file_content: str, blocks: List[SearchReplaceBlock]) -> str:
    """
    Apply search-replace blocks in order. First occurrence of each search text
    is replaced; if not found, that block is skipped.
    """
    result = file_content
    for b in blocks:
        if b.search not in result:
            continue
        result = result.replace(b.search, b.replace, 1)
    return result


def parse_search_replace_blocks(text: str) -> List[SearchReplaceBlock]:
    """
    Parse LLM output containing <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE blocks.
    Returns list of SearchReplaceBlock; order preserved.
    """
    blocks: List[SearchReplaceBlock] = []
    for m in _SEARCH_REPLACE_RE.finditer(text):
        blocks.append(SearchReplaceBlock(search=m.group(1).strip(), replace=m.group(2).strip()))
    return blocks


def changes_to_unified_diff_for_files(
    root_dir: str,
    changes: List[CodeChange],
    context_lines: int = 3,
) -> str:
    """
    Group changes by fpath, compute old/new content per file, produce one unified diff.
    Returns concatenated diff for all modified files (suitable for API response).
    """
    from api.semantic_tree.pipeline.code_applicator import _apply_changes_to_content

    path = Path(root_dir)
    by_file: dict[str, list[CodeChange]] = {}
    for c in changes:
        by_file.setdefault(c.fpath, []).append(c)

    diffs: List[str] = []
    for fpath, file_changes in sorted(by_file.items()):
        full = path / fpath
        if not full.is_file():
            continue
        old_content = full.read_text(encoding="utf-8", errors="replace")
        sorted_changes = sorted(file_changes, key=lambda x: (x.line_start, x.line_end or 0), reverse=True)
        new_content = _apply_changes_to_content(old_content, sorted_changes)
        diff = code_changes_to_unified_diff(fpath, old_content, new_content, context_lines=context_lines)
        if diff:
            diffs.append(diff)
    return "\n".join(diffs)
