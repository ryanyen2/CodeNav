"""Which files the index walks."""
from __future__ import annotations

import json
from pathlib import PurePosixPath

import pytest

from codoc.index.app import code_on_disk, source_matcher, why_not_indexed


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


def test_a_path_spelled_with_another_case_names_no_file_the_index_reads(tmp_path):
    """On a case-insensitive filesystem (macOS and Windows by default)
    `SRC/rate.py` opens `src/rate.py`, but the walk keys that file
    `src/rate.py` only. A binding under another spelling would be a key the
    index never produces, so no file is read under it."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "rate.py").write_text("def limit():\n    return 1\n")
    if not (tmp_path / "SRC" / "rate.py").exists():
        pytest.skip("a case-sensitive filesystem: the other spelling names nothing")
    assert set(code_on_disk(tmp_path, {"src/rate.py", "SRC/rate.py", "src/RATE.py"})) == {"src/rate.py::limit"}
    assert "spelled" in why_not_indexed(tmp_path, "SRC/rate.py")
