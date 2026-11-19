"""Tests for Python and TypeScript language adapters."""

from __future__ import annotations

from pathlib import Path

import pytest

from codoc.index.tree_walk import walk
from codoc.index.lang import detect_language, get_adapter


def fingerprint_chunk(source: str, adapter) -> str:
    """The chunk's fingerprint, which ignores whitespace and comments."""
    return walk(source, adapter).fingerprint


def test_detect_language_python() -> None:
    assert detect_language("/path/to/file.py") == "python"


def test_detect_language_typescript() -> None:
    assert detect_language("/path/to/file.ts") == "typescript"
    assert detect_language("/path/to/file.tsx") == "tsx"


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
    paths = {c.symbol for c in chunks}
    assert "tests/fixtures/sample_cli.py::create_parser" in paths
    assert "tests/fixtures/sample_cli.py::ArgEnum" in paths
    assert "tests/fixtures/sample_cli.py::ArgEnum.from_string" in paths


def test_python_adapter_chunk_anchor_uniqueness(fixtures_dir: Path) -> None:
    adapter = get_adapter("python")
    src = (fixtures_dir / "sample_cli.py").read_text()
    chunks = adapter.extract_chunks("tests/fixtures/sample_cli.py", src)
    paths = [c.symbol for c in chunks]
    assert len(paths) == len(set(paths))


def test_python_adapter_fingerprint_stable_under_whitespace(fixtures_dir: Path) -> None:
    adapter = get_adapter("python")
    src = (fixtures_dir / "sample_cli.py").read_text()
    chunks = adapter.extract_chunks("tests/fixtures/sample_cli.py", src)
    target = next(c for c in chunks if c.symbol.endswith("::create_parser"))
    fp_orig = fingerprint_chunk(target.source, adapter)
    spaced = "\n\n# leading comment\n" + target.source.replace("\n", "\n  \n", 1) + "\n"
    fp_spaced = fingerprint_chunk(spaced, adapter)
    assert fp_orig == fp_spaced


def test_typescript_adapter_extracts_chunks(fixtures_dir: Path) -> None:
    adapter = get_adapter("typescript")
    src = (fixtures_dir / "sample_app.ts").read_text()
    chunks = adapter.extract_chunks("tests/fixtures/sample_app.ts", src)
    assert len(chunks) > 0
    paths = {c.symbol for c in chunks}
    assert "tests/fixtures/sample_app.ts::Coordinator" in paths
    assert "tests/fixtures/sample_app.ts::Coordinator.query" in paths
    assert "tests/fixtures/sample_app.ts::makeOptions" in paths
    assert "tests/fixtures/sample_app.ts::ClientOptions" in paths


def test_typescript_adapter_chunk_anchor_uniqueness(fixtures_dir: Path) -> None:
    adapter = get_adapter("typescript")
    src = (fixtures_dir / "sample_app.ts").read_text()
    chunks = adapter.extract_chunks("tests/fixtures/sample_app.ts", src)
    paths = [c.symbol for c in chunks]
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
    paths = [c.symbol for c in chunks]
    assert len(paths) == len(set(paths))
    assert any(p.endswith("::__module__") for p in paths)


def test_python_overload_stubs_give_way_to_the_implementation() -> None:
    src = ("from typing import overload\n\n@overload\ndef command(name: str) -> int: ...\n"
           "@overload\ndef command(name: None) -> str: ...\ndef command(name=None):\n    return 42\n")
    chunks = [c for c in get_adapter("python").extract_chunks("c.py", src) if c.symbol == "c.py::command"]
    assert [c.source for c in chunks] == ["def command(name=None):\n    return 42"]


def test_typescript_private_methods_are_chunks() -> None:
    src = "class Ky {\n  async #run() { return this.#retry(); }\n  #retry() { return 1; }\n}\n"
    symbols = {c.symbol for c in get_adapter("typescript").extract_chunks("k.ts", src)}
    assert {"k.ts::Ky.#run", "k.ts::Ky.#retry"} <= symbols


def test_a_tsx_file_is_parsed_as_tsx_so_jsx_hides_no_component() -> None:
    """The TypeScript grammar reads JSX as syntax errors, which swallowed App
    after the arrow component before it. The TSX grammar parses the file
    cleanly, and a JSX element is a call to its component; an HTML element
    (`<div>`) is not."""
    src = ("export const Label = ({ text }: { text: string }) => <span>{text}</span>;\n\n"
           "export default function App() {\n  return <div><Label text=\"hi\" /><ui.Panel /></div>;\n}\n")
    adapter = get_adapter(detect_language("c.tsx"))
    assert not adapter.parse(src).root_node.has_error
    chunks = {c.symbol: c for c in adapter.extract_chunks("c.tsx", src)}
    assert {"c.tsx::Label", "c.tsx::App"} <= set(chunks)
    refs = adapter.references_in_chunk(chunks["c.tsx::App"].source, "c.tsx")
    assert [(r.qualified_name, r.ref_kind) for r in refs] == [("Label", "call"), ("ui.Panel", "call")]
