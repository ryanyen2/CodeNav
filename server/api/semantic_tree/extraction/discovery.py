"""Directory discovery with configurable exclusions (references api.config defaults)."""

import os
import fnmatch
import logging
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Python extensions for entity extraction
PYTHON_EXT = (".py",)


def _norm_path(path: str, root: str) -> str:
    """Return path relative to root, normalized."""
    p = Path(path).resolve()
    r = Path(root).resolve()
    try:
        return str(p.relative_to(r)).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


def _is_excluded_dir(rel_dir: str, excluded_dirs: List[str]) -> bool:
    """Check if directory (relative to root) is excluded."""
    for pat in excluded_dirs:
        pat = pat.lstrip("./").rstrip("/")
        if not pat:
            continue
        if rel_dir == pat or rel_dir.startswith(pat + "/") or "/" + pat + "/" in "/" + rel_dir:
            return True
        if fnmatch.fnmatch(rel_dir, pat) or fnmatch.fnmatch(rel_dir, pat + "/*"):
            return True
    return False


def _is_excluded_file(filename: str, excluded_files: List[str]) -> bool:
    """Check if filename is excluded by pattern list."""
    for pat in excluded_files:
        if fnmatch.fnmatch(filename, pat) or fnmatch.fnmatch(filename.lower(), pat.lower()):
            return True
    return False


def discover_files(
    root_dir: str,
    excluded_dirs: Optional[List[str]] = None,
    excluded_files: Optional[List[str]] = None,
    extensions: Tuple[str, ...] = PYTHON_EXT,
) -> List[Tuple[str, str]]:
    """
    Walk root_dir and return list of (relative_fpath, language) for files matching extensions.
    Uses api.config DEFAULT_EXCLUDED_* when excluded_* are None.
    """
    from api.config import DEFAULT_EXCLUDED_DIRS, DEFAULT_EXCLUDED_FILES

    excluded_dirs = excluded_dirs if excluded_dirs is not None else DEFAULT_EXCLUDED_DIRS
    excluded_files = excluded_files if excluded_files is not None else DEFAULT_EXCLUDED_FILES

    root = Path(root_dir).resolve()
    if not root.is_dir():
        logger.warning("discover_files: root_dir is not a directory: %s", root_dir)
        return []

    result: List[Tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = _norm_path(dirpath, str(root))
        if rel_dir == ".":
            rel_dir = ""

        # Prune excluded dirs
        to_remove = []
        for d in dirnames:
            if d.startswith(".") or d == "__pycache__" or d == "node_modules":
                to_remove.append(d)
            else:
                sub = f"{rel_dir}/{d}" if rel_dir else d
                if _is_excluded_dir(sub, excluded_dirs):
                    to_remove.append(d)
        for d in to_remove:
            dirnames.remove(d)

        for f in filenames:
            if f.startswith("."):
                continue
            if _is_excluded_file(f, excluded_files):
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext not in extensions:
                continue
            lang = "python" if ext == ".py" else "unknown"
            rel_path = f"{rel_dir}/{f}" if rel_dir else f
            result.append((rel_path, lang))

    return result
