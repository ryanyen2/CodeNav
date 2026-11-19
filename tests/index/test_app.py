"""Which files the index walks."""
from __future__ import annotations

import json
from pathlib import PurePosixPath

from codoc.index.app import source_matcher


def _walks(root, path: str) -> bool:
    return source_matcher(root).is_file_included(PurePosixPath(path))


def test_build_output_is_excluded_at_the_root_only(tmp_path):
    assert not _walks(tmp_path, "build/x.py")
    assert _walks(tmp_path, "src/build/x.py")


def test_tests_are_excluded_until_index_exclude_says_otherwise(tmp_path):
    assert not _walks(tmp_path, "tests/test_a.py") and not _walks(tmp_path, "src/a.test.ts")
    (tmp_path / ".codoc").mkdir()
    (tmp_path / ".codoc" / "config.json").write_text(json.dumps({"index_exclude": ["vendored/"]}))
    assert _walks(tmp_path, "tests/test_a.py") and not _walks(tmp_path, "lib/vendored/x.py")
