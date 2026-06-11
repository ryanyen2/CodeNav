"""Tests for Python and TypeScript language adapters."""

from __future__ import annotations

from pathlib import Path

import pytest

from codoc.core.tree_walk import walk
from codoc.lang import detect_language, get_adapter


def fingerprint_chunk(source: str, adapter) -> str:
    """Whitespace-/comment-stable token fingerprint (tree_walk tokens_hash)."""
    return walk(source, adapter).tokens_hash


def test_detect_language_python() -> None:
    assert detect_language("/path/to/file.py") == "python"


def test_detect_language_typescript() -> None:
    assert detect_language("/path/to/file.ts") == "typescript"
    assert detect_language("/path/to/file.tsx") == "typescript"


def test_detect_language_unsupported_returns_none() -> None:
    assert detect_language("README.md") is None


def test_get_adapter_unknown_raises() -> None:
    with pytest.raises(ValueError):
        get_adapter("klingon")


def test_python_adapter_extracts_chunks(fixtures_dir: Path) -> None:
    adapter = get_adapter("python")
    src = (fixtures_dir / "sample_cli.py").read_text()
    chunks = adapter.extract_chunks("tests/fixtures/sample_cli.py", src)
    assert len(chunks) > 0
    paths = {c.symbol_path for c in chunks}
    assert "tests/fixtures/sample_cli.py::create_parser" in paths
    assert "tests/fixtures/sample_cli.py::ArgEnum" in paths
    assert "tests/fixtures/sample_cli.py::ArgEnum.from_string" in paths


def test_python_adapter_chunk_anchor_uniqueness(fixtures_dir: Path) -> None:
    adapter = get_adapter("python")
    src = (fixtures_dir / "sample_cli.py").read_text()
    chunks = adapter.extract_chunks("tests/fixtures/sample_cli.py", src)
    paths = [c.symbol_path for c in chunks]
    assert len(paths) == len(set(paths))


def test_python_adapter_fingerprint_stable_under_whitespace(fixtures_dir: Path) -> None:
    adapter = get_adapter("python")
    src = (fixtures_dir / "sample_cli.py").read_text()
    chunks = adapter.extract_chunks("tests/fixtures/sample_cli.py", src)
    target = next(c for c in chunks if c.symbol_path.endswith("::create_parser"))
    fp_orig = fingerprint_chunk(target.source, adapter)
    spaced = "\n\n# leading comment\n" + target.source.replace("\n", "\n  \n", 1) + "\n"
    fp_spaced = fingerprint_chunk(spaced, adapter)
    assert fp_orig == fp_spaced


def test_typescript_adapter_extracts_chunks(fixtures_dir: Path) -> None:
    adapter = get_adapter("typescript")
    src = (fixtures_dir / "sample_app.ts").read_text()
    chunks = adapter.extract_chunks("tests/fixtures/sample_app.ts", src)
    assert len(chunks) > 0
    paths = {c.symbol_path for c in chunks}
    assert "tests/fixtures/sample_app.ts::Coordinator" in paths
    assert "tests/fixtures/sample_app.ts::Coordinator.query" in paths
    assert "tests/fixtures/sample_app.ts::makeOptions" in paths
    assert "tests/fixtures/sample_app.ts::ClientOptions" in paths


def test_typescript_adapter_chunk_anchor_uniqueness(fixtures_dir: Path) -> None:
    adapter = get_adapter("typescript")
    src = (fixtures_dir / "sample_app.ts").read_text()
    chunks = adapter.extract_chunks("tests/fixtures/sample_app.ts", src)
    paths = [c.symbol_path for c in chunks]
    assert len(paths) == len(set(paths))


def test_typescript_adapter_fingerprint_stable_under_whitespace(fixtures_dir: Path) -> None:
    adapter = get_adapter("typescript")
    src = (fixtures_dir / "sample_app.ts").read_text()
    chunks = adapter.extract_chunks("tests/fixtures/sample_app.ts", src)
    assert chunks
    target = chunks[0]
    fp_orig = fingerprint_chunk(target.source, adapter)
    spaced = target.source.replace("\n", "\n   \n")
    fp_spaced = fingerprint_chunk(spaced, adapter)
    assert fp_orig == fp_spaced


def test_typescript_adapter_reexport_barrel_module_chunk(fixtures_dir: Path) -> None:
    """A pure re-export barrel produces a single ``__module__`` chunk."""
    adapter = get_adapter("typescript")
    src = (fixtures_dir / "sample_index.ts").read_text()
    chunks = adapter.extract_chunks("tests/fixtures/sample_index.ts", src)
    assert len(chunks) > 0
    paths = [c.symbol_path for c in chunks]
    assert len(paths) == len(set(paths))
    assert any(p.endswith("::__module__") for p in paths)
