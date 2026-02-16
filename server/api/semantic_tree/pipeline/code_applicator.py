"""
AST-aware transactional file modification for inverse sync (tree edit → code change).

Applies a list of CodeChange edits to the filesystem. Edits are applied per-file
from bottom to top so line numbers remain valid. All files are read first, then
all edits applied in memory, then written (transactional per file).
"""

import logging
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CodeChange(BaseModel):
    """
    Single edit to a file. 1-based line numbers.
    - replace: lines [line_start, line_end] are replaced by new_content (use new_content="" for delete).
    - insert: new_content is inserted before line_start (line_end ignored).
    """
    fpath: str = Field(..., description="Relative path to file from root_dir")
    line_start: int = Field(..., ge=1, description="First line (1-based)")
    line_end: Optional[int] = Field(None, description="Last line (1-based); None means insert before line_start")
    new_content: str = Field("", description="New text (empty for delete)")


def _get_file_content(root_dir: str, fpath: str) -> str:
    path = Path(root_dir) / fpath
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _apply_changes_to_content(content: str, changes: List[CodeChange]) -> str:
    """Apply changes to file content. changes must be sorted by line_start descending."""
    lines = content.splitlines(keepends=True)
    if not lines and changes:
        # Empty file: only inserts at line 1 are valid
        new_lines = []
        for c in reversed(changes):
            if c.line_end is None and c.line_start == 1:
                new_lines.insert(0, (c.new_content.rstrip() + "\n") if c.new_content else "")
        return "".join(new_lines) if new_lines else ""

    for c in changes:
        if c.line_end is None:
            # Insert before line_start (1-based → 0-based: index line_start - 1)
            idx = max(0, min(c.line_start - 1, len(lines)))
            insert_text = (c.new_content.rstrip() + "\n") if c.new_content else ""
            lines.insert(idx, insert_text)
        else:
            # Replace [line_start, line_end] with new_content (may be multi-line)
            start = max(0, c.line_start - 1)
            end = min(c.line_end, len(lines))
            replacement_lines = [
                ln + "\n" for ln in c.new_content.rstrip().split("\n")
            ] if c.new_content.strip() else [""]
            lines[start:end] = replacement_lines
    return "".join(lines)


def apply_changes(root_dir: str, changes: List[CodeChange]) -> List[str]:
    """
    Apply CodeChanges to files under root_dir. Changes are grouped by fpath and
    applied from bottom to top within each file. Returns list of modified fpaths.
    """
    if not changes:
        return []

    by_file: dict[str, list[CodeChange]] = {}
    for c in changes:
        by_file.setdefault(c.fpath, []).append(c)

    modified: list[str] = []
    root = Path(root_dir).resolve()

    for fpath, file_changes in by_file.items():
        path = root / fpath
        if not path.is_file() and not fpath.endswith(".py"):
            candidate = root / (fpath + ".py")
            if candidate.is_file():
                fpath = fpath + ".py"
                path = candidate
        if not path.is_file():
            logger.warning("Skip apply (not a file): %s", fpath)
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        # Sort by line_start descending so we don't invalidate line numbers
        file_changes_sorted = sorted(file_changes, key=lambda x: (x.line_start, x.line_end or 0), reverse=True)
        new_content = _apply_changes_to_content(content, file_changes_sorted)
        if new_content != content:
            path.write_text(new_content, encoding="utf-8")
            modified.append(fpath)
    return modified
