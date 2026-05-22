"""IgnoreRules — single source of truth for what codoc indexes.

Layers (highest priority first):
1. Hard-coded prelude (generated dirs, caches, build outputs).
2. .codocignore (pathspec, same syntax as .gitignore).
3. .gitignore (pathspec, nested gitignores honored).
4. Linguist-style markers from .gitattributes (linguist-generated, linguist-vendored).
5. File-size cap (default 1 MiB).
6. Binary sniff (NUL bytes in leading 2 KB).

Usage:
    rules = IgnoreRules.for_root(root_dir)
    should_index, reason = rules.should_index(path)
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_PRELUDE_PATTERNS = [
    ".git",
    ".codoc",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".tox",
    ".nuxt",
    ".next",
    "target",
    "build",
    "dist",
    "out",
    "bin",
    "obj",
    ".gradle",
    ".idea",
    ".vscode",
    ".cache",
    ".parcel-cache",
    ".turbo",
    "coverage",
    "htmlcov",
    "vendor",
    "Pods",
    "DerivedData",
    ".terraform",
    ".serverless",
    "tmp",
    ".DS_Store",
    "*.egg-info",
    "*.egg-info/**",
]

DEFAULT_MAX_BYTES = 1 * 1024 * 1024  # 1 MiB


class IgnoreRules:
    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self._root = root.resolve()
        self._max_bytes = max_bytes
        self._prelude = self._build_prelude()
        self._gitignore = self._load_pathspec(root / ".gitignore")
        self._codocignore = self._load_pathspec(root / ".codocignore")
        self._linguist_generated: set[str] = set()
        self._linguist_vendored: set[str] = set()
        self._load_gitattributes(root / ".gitattributes")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_index(self, path: str | Path) -> tuple[bool, str]:
        """Return (True, '') if *path* should be indexed, else (False, reason)."""
        p = Path(path).resolve()
        try:
            rel = str(p.relative_to(self._root))
        except ValueError:
            return False, "outside_root"

        rel_posix = Path(rel).as_posix()

        if self._prelude.match_file(rel_posix):
            return False, "prelude"

        if self._gitignore and self._gitignore.match_file(rel_posix):
            return False, "gitignore"

        if self._codocignore and self._codocignore.match_file(rel_posix):
            return False, "codocignore"

        if rel_posix in self._linguist_generated:
            return False, "linguist_generated"
        if rel_posix in self._linguist_vendored:
            return False, "linguist_vendored"

        if p.is_file():
            try:
                size = p.stat().st_size
            except OSError:
                return False, "stat_error"
            if size > self._max_bytes:
                return False, "too_large"
            if size > 0:
                try:
                    header = p.read_bytes()[:2048]
                    if b"\x00" in header:
                        return False, "binary"
                except OSError:
                    return False, "read_error"

        return True, ""

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def for_root(cls, root_dir: str | Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> "IgnoreRules":
        return cls(Path(root_dir), max_bytes=max_bytes)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_prelude(self):
        try:
            import pathspec
            return pathspec.PathSpec.from_lines("gitwildmatch", _PRELUDE_PATTERNS)
        except ImportError:
            return _NullSpec()

    def _load_pathspec(self, path: Path):
        if not path.is_file():
            return None
        try:
            import pathspec
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            return pathspec.PathSpec.from_lines("gitwildmatch", lines)
        except ImportError:
            return None
        except OSError:
            return None

    def _load_gitattributes(self, path: Path) -> None:
        if not path.is_file():
            return
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                pattern = parts[0]
                attrs = parts[1:]
                if "linguist-generated=true" in attrs or "linguist-generated" in attrs:
                    self._linguist_generated.add(pattern)
                if "linguist-vendored=true" in attrs or "linguist-vendored" in attrs:
                    self._linguist_vendored.add(pattern)
        except OSError:
            pass


class _NullSpec:
    """Fallback when pathspec is not installed — matches nothing."""

    def match_file(self, path: str) -> bool:
        return False
