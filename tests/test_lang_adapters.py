"""Tests for Python and TypeScript language adapters."""

from __future__ import annotations

from pathlib import Path

import pytest

from codoc.core.fingerprint import fingerprint_chunk
from codoc.lang import detect_language, get_adapter


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


def test_python_adapter_extracts_chunks(draco_dir: Path) -> None:
    adapter = get_adapter("python")
    src = (draco_dir / "cli.py").read_text()
    chunks = adapter.extract_chunks("test/draco/cli.py", src)
    assert len(chunks) > 0
    paths = {c.symbol_path for c in chunks}
    assert "test/draco/cli.py::create_parser" in paths
    assert "test/draco/cli.py::ArgEnum" in paths
    assert "test/draco/cli.py::ArgEnum.from_string" in paths


def test_python_adapter_chunk_anchor_uniqueness(draco_dir: Path) -> None:
    adapter = get_adapter("python")
    src = (draco_dir / "cli.py").read_text()
    chunks = adapter.extract_chunks("test/draco/cli.py", src)
    paths = [c.symbol_path for c in chunks]
    assert len(paths) == len(set(paths))


def test_python_adapter_fingerprint_stable_under_whitespace(draco_dir: Path) -> None:
    adapter = get_adapter("python")
    src = (draco_dir / "cli.py").read_text()
    chunks = adapter.extract_chunks("test/draco/cli.py", src)
    target = next(c for c in chunks if c.symbol_path.endswith("::create_parser"))
    fp_orig = fingerprint_chunk(target.source, adapter)
    # Add some whitespace and a comment to force a re-extraction.
    spaced = "\n\n# leading comment\n" + target.source.replace("\n", "\n  \n", 1) + "\n"
    fp_spaced = fingerprint_chunk(spaced, adapter)
    assert fp_orig == fp_spaced


def test_typescript_adapter_extracts_chunks(mosaic_dir: Path) -> None:
    adapter = get_adapter("typescript")
    src_path = mosaic_dir / "src" / "Coordinator.ts"
    src = src_path.read_text()
    chunks = adapter.extract_chunks("test/mosaic/src/Coordinator.ts", src)
    assert len(chunks) > 0
    paths = {c.symbol_path for c in chunks}
    # Coordinator class should be present.
    assert any("::Coordinator" in p for p in paths)


def test_typescript_adapter_chunk_anchor_uniqueness(mosaic_dir: Path) -> None:
    adapter = get_adapter("typescript")
    src = (mosaic_dir / "src" / "Coordinator.ts").read_text()
    chunks = adapter.extract_chunks("test/mosaic/src/Coordinator.ts", src)
    paths = [c.symbol_path for c in chunks]
    assert len(paths) == len(set(paths))


def test_typescript_adapter_fingerprint_stable_under_whitespace(mosaic_dir: Path) -> None:
    adapter = get_adapter("typescript")
    src = (mosaic_dir / "src" / "Coordinator.ts").read_text()
    chunks = adapter.extract_chunks("test/mosaic/src/Coordinator.ts", src)
    assert chunks
    target = chunks[0]
    fp_orig = fingerprint_chunk(target.source, adapter)
    spaced = target.source.replace("\n", "\n   \n")
    fp_spaced = fingerprint_chunk(spaced, adapter)
    assert fp_orig == fp_spaced


def test_python_adapter_index_ts_only_module_chunk(mosaic_dir: Path) -> None:
    """The plan-required test: chunks > 0 for index.ts.

    The mosaic index.ts is pure re-exports → produces one ``__module__`` chunk.
    """
    adapter = get_adapter("typescript")
    src = (mosaic_dir / "src" / "index.ts").read_text()
    chunks = adapter.extract_chunks("test/mosaic/src/index.ts", src)
    assert len(chunks) > 0
    paths = [c.symbol_path for c in chunks]
    assert len(paths) == len(set(paths))
