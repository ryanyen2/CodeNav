"""Tests for anchor resolution against test/draco/cli.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from codoc.core.anchor_resolver import resolve_anchor
from codoc.lang import get_adapter
from codoc.model.anchor import Anchor


@pytest.fixture
def cli_source(draco_dir: Path) -> str:
    return (draco_dir / "cli.py").read_text()


def test_resolve_symbol_path_for_top_level_function(cli_source: str) -> None:
    adapter = get_adapter("python")
    anchor = Anchor(
        file="test/draco/cli.py",
        symbol_path="test/draco/cli.py::create_parser",
    )
    byte_range = resolve_anchor(anchor, cli_source, adapter)
    assert byte_range is not None
    start, end = byte_range
    assert start < end
    chunk = cli_source.encode("utf-8")[start:end].decode("utf-8")
    assert chunk.lstrip().startswith("def create_parser")


def test_resolve_symbol_path_for_nested_method(cli_source: str) -> None:
    adapter = get_adapter("python")
    anchor = Anchor(
        file="test/draco/cli.py",
        symbol_path="test/draco/cli.py::ArgEnum.from_string",
    )
    byte_range = resolve_anchor(anchor, cli_source, adapter)
    assert byte_range is not None
    start, end = byte_range
    chunk = cli_source.encode("utf-8")[start:end].decode("utf-8")
    assert "from_string" in chunk
    assert chunk.lstrip().startswith("@staticmethod") or chunk.lstrip().startswith("def from_string")


def test_resolve_symbol_path_failure_returns_none(cli_source: str) -> None:
    adapter = get_adapter("python")
    anchor = Anchor(
        file="test/draco/cli.py",
        symbol_path="test/draco/cli.py::NonExistentEntity",
    )
    assert resolve_anchor(anchor, cli_source, adapter) is None


def test_resolve_returns_none_for_unparseable_with_only_query() -> None:
    adapter = get_adapter("python")
    # Use ts_query that won't match anything in this code.
    anchor = Anchor(
        file="test/draco/cli.py",
        ts_query="(import_statement) @x",
    )
    src = "x = 1\n"
    # Even if it matches, ts_query may need at least one match; ensure no error.
    result = resolve_anchor(anchor, src, adapter)
    assert result is None


def test_resolve_symbol_path_occurrence_index_disambiguates(cli_source: str) -> None:
    """occurrence_index is a stable disambiguator on the Anchor; the resolver
    treats different occurrence_index values as logically distinct anchors and
    only differentiates when ts_query is involved.  We verify here that two
    anchors with the same symbol_path and different occurrence_index resolve
    consistently to the same byte range (since symbol_path alone defines
    a unique entity)."""
    adapter = get_adapter("python")
    a0 = Anchor(
        file="test/draco/cli.py",
        symbol_path="test/draco/cli.py::create_parser",
        occurrence_index=0,
    )
    a1 = Anchor(
        file="test/draco/cli.py",
        symbol_path="test/draco/cli.py::create_parser",
        occurrence_index=5,
    )
    r0 = resolve_anchor(a0, cli_source, adapter)
    r1 = resolve_anchor(a1, cli_source, adapter)
    assert r0 is not None and r1 is not None
    assert r0 == r1


def test_resolve_returns_none_when_neither_locator_resolves(cli_source: str) -> None:
    adapter = get_adapter("python")
    anchor = Anchor(
        file="test/draco/cli.py",
        symbol_path="test/draco/cli.py::DefinitelyNotPresent",
    )
    assert resolve_anchor(anchor, cli_source, adapter) is None
