"""Tests for anchor resolution against tests/fixtures/sample_cli.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from codoc.core.anchor_resolver import resolve_anchor
from codoc.lang import get_adapter
from codoc.model.anchor import Anchor

_FILE = "tests/fixtures/sample_cli.py"


@pytest.fixture
def cli_source(fixtures_dir: Path) -> str:
    return (fixtures_dir / "sample_cli.py").read_text()


def test_resolve_symbol_path_for_top_level_function(cli_source: str) -> None:
    adapter = get_adapter("python")
    anchor = Anchor(file=_FILE, symbol_path=f"{_FILE}::create_parser")
    byte_range = resolve_anchor(anchor, cli_source, adapter)
    assert byte_range is not None
    start, end = byte_range
    assert start < end
    chunk = cli_source.encode("utf-8")[start:end].decode("utf-8")
    assert chunk.lstrip().startswith("def create_parser")


def test_resolve_symbol_path_for_nested_method(cli_source: str) -> None:
    adapter = get_adapter("python")
    anchor = Anchor(file=_FILE, symbol_path=f"{_FILE}::ArgEnum.from_string")
    byte_range = resolve_anchor(anchor, cli_source, adapter)
    assert byte_range is not None
    start, end = byte_range
    chunk = cli_source.encode("utf-8")[start:end].decode("utf-8")
    assert "from_string" in chunk
    assert chunk.lstrip().startswith("@staticmethod") or chunk.lstrip().startswith("def from_string")


def test_resolve_symbol_path_failure_returns_none(cli_source: str) -> None:
    adapter = get_adapter("python")
    anchor = Anchor(file=_FILE, symbol_path=f"{_FILE}::NonExistentEntity")
    assert resolve_anchor(anchor, cli_source, adapter) is None


def test_resolve_returns_none_for_unparseable_with_only_query() -> None:
    adapter = get_adapter("python")
    anchor = Anchor(
        file=_FILE,
        ts_query="(import_statement) @x",
    )
    src = "x = 1\n"
    result = resolve_anchor(anchor, src, adapter)
    assert result is None


def test_resolve_symbol_path_occurrence_index_disambiguates(cli_source: str) -> None:
    """Two anchors with same symbol_path but different occurrence_index resolve
    to the same byte range (symbol_path alone uniquely identifies an entity)."""
    adapter = get_adapter("python")
    a0 = Anchor(file=_FILE, symbol_path=f"{_FILE}::create_parser", occurrence_index=0)
    a1 = Anchor(file=_FILE, symbol_path=f"{_FILE}::create_parser", occurrence_index=5)
    r0 = resolve_anchor(a0, cli_source, adapter)
    r1 = resolve_anchor(a1, cli_source, adapter)
    assert r0 is not None and r1 is not None
    assert r0 == r1


def test_resolve_returns_none_when_neither_locator_resolves(cli_source: str) -> None:
    adapter = get_adapter("python")
    anchor = Anchor(file=_FILE, symbol_path=f"{_FILE}::DefinitelyNotPresent")
    assert resolve_anchor(anchor, cli_source, adapter) is None
