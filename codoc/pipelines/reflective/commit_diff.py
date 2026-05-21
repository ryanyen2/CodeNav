"""Git diff detection for the reflective pipeline.

Finds which files changed between two refs and extracts their current chunks
using the appropriate language adapter.  Only files whose language is
supported by codoc.lang are included.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from codoc.lang import get_adapter, detect_language, Chunk


def get_changed_files(
    root_dir: str,
    from_ref: str = "HEAD~1",
    to_ref: str = "HEAD",
) -> list[str]:
    """Return repo-relative paths of files that changed between *from_ref* and *to_ref*.

    If *from_ref* is not a valid git ref (e.g. the very first commit has no
    parent), falls back to ``git ls-files`` to return every tracked file so
    that the reflective pipeline bootstraps correctly on an initial commit.
    """
    result = subprocess.run(
        ["git", "diff", "--name-only", from_ref, to_ref],
        cwd=root_dir,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        # from_ref doesn't exist (initial commit, shallow clone, etc.).
        # Fall back: treat every tracked file as changed.
        fallback = subprocess.run(
            ["git", "ls-files"],
            cwd=root_dir,
            capture_output=True,
            text=True,
        )
        if fallback.returncode != 0:
            return []
        all_paths = [p.strip() for p in fallback.stdout.splitlines() if p.strip()]
        return [p for p in all_paths if not p.startswith(".codoc/")]

    # Invariant 2: exclude .codoc/ from diff so reflect never re-fires on its own writes.
    all_paths = [p.strip() for p in result.stdout.splitlines() if p.strip()]
    return [p for p in all_paths if not p.startswith(".codoc/")]


def get_file_source(root_dir: str, file_path: str) -> str | None:
    """Read the current on-disk content of *file_path* (repo-relative).

    Returns ``None`` if the file does not exist (deleted in this commit).
    """
    abs_path = Path(root_dir) / file_path
    if not abs_path.exists():
        return None
    try:
        return abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def extract_chunks_for_files(
    root_dir: str,
    file_paths: list[str],
) -> dict[str, list[Chunk]]:
    """Extract chunks for each file using the appropriate language adapter.

    Parameters
    ----------
    root_dir:
        Absolute path to the repository root.
    file_paths:
        Repo-relative file paths to process (e.g. from :func:`get_changed_files`).

    Returns
    -------
    dict[str, list[Chunk]]
        Mapping from repo-relative file path → list of extracted chunks.
        Files that are deleted (source is ``None``), unsupported, or cause
        an extraction error are omitted from the result.
    """
    result: dict[str, list[Chunk]] = {}

    for file_path in file_paths:
        language = detect_language(file_path)
        if language is None:
            # Unsupported extension — skip silently.
            continue

        source = get_file_source(root_dir, file_path)
        if source is None:
            # Deleted file — caller is responsible for treating it as removed.
            continue

        try:
            adapter = get_adapter(language)
            chunks = adapter.extract_chunks(file_path, source)
        except Exception:
            # If extraction fails for any reason, skip this file rather than
            # crashing the whole pipeline.
            continue

        result[file_path] = chunks

    return result
