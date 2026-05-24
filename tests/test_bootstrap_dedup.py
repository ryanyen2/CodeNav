"""Unit tests for bootstrap deduplication fixes.

Tests cover:
1. cluster_into_parents: each original group belongs to exactly one parent
2. Python adapter: simple public assignments emit file::NAME chunks
3. TypeScript adapter: simple named const/let declarations emit file::NAME chunks
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest

from codoc.pipelines.bootstrap.semantic_cluster import (
    SemanticGroup,
    cluster_into_parents,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_group(group_id: int, files: list[str], chunk_start: int = 0) -> SemanticGroup:
    """Build a leaf SemanticGroup with synthetic chunk indices."""
    n = len(files)
    return SemanticGroup(
        group_id=group_id,
        file_paths=files,
        chunk_indices=list(range(chunk_start, chunk_start + n)),
    )


# ---------------------------------------------------------------------------
# cluster_into_parents: group-level clustering guarantees no shared groups
# ---------------------------------------------------------------------------


class TestClusterIntoParentsNoGroupSharing:
    """After fix #2, each original group must appear in exactly one parent."""

    def _build_groups(self) -> list[SemanticGroup]:
        """Return 7 distinct groups to trigger the >6 merge path."""
        return [
            _make_group(i, [f"pkg_{i}/module_{i}.py", f"pkg_{i}/helpers_{i}.py"], chunk_start=i * 2)
            for i in range(1, 8)
        ]

    def test_each_group_in_exactly_one_parent(self) -> None:
        groups = self._build_groups()

        # Provide minimal chunks that _file_summaries can handle (empty source is ok)
        from codoc.lang.base import Chunk

        chunks = []
        for g in groups:
            for f in g.file_paths:
                chunks.append(
                    Chunk(symbol_path=f"{f}::__module__", file=f, start_byte=0, end_byte=0, source="")
                )

        parents = cluster_into_parents(groups, chunks, root_dir="", n_target=3)

        # Collect all child group_ids across all parents.
        child_group_ids: list[int] = []
        for p in parents:
            for cg in p.children:
                child_group_ids.append(cg.group_id)

        original_ids = {g.group_id for g in groups}

        # Every original group must appear exactly once.
        assert set(child_group_ids) == original_ids, (
            f"Not all original groups represented: {original_ids - set(child_group_ids)}"
        )
        assert len(child_group_ids) == len(original_ids), (
            f"Duplicate group membership detected — "
            f"{len(child_group_ids)} entries for {len(original_ids)} groups"
        )

    def test_no_groups_repeated_across_parents(self) -> None:
        groups = self._build_groups()

        from codoc.lang.base import Chunk

        chunks = [
            Chunk(symbol_path=f"{f}::__module__", file=f, start_byte=0, end_byte=0, source="")
            for g in groups
            for f in g.file_paths
        ]

        parents = cluster_into_parents(groups, chunks, root_dir="", n_target=3)

        seen: set[int] = set()
        for p in parents:
            for cg in p.children:
                assert cg.group_id not in seen, (
                    f"Group {cg.group_id} appears in multiple parents"
                )
                seen.add(cg.group_id)

    def test_returns_groups_unchanged_when_already_few_enough(self) -> None:
        groups = self._build_groups()[:3]

        from codoc.lang.base import Chunk

        chunks = [
            Chunk(symbol_path=f"{f}::__module__", file=f, start_byte=0, end_byte=0, source="")
            for g in groups
            for f in g.file_paths
        ]

        result = cluster_into_parents(groups, chunks, n_target=5)
        assert result is groups  # returned as-is when len(groups) <= n_target


# ---------------------------------------------------------------------------
# Python adapter: named assignments emit file::NAME chunks
# ---------------------------------------------------------------------------


class TestPythonAdapterNamedAssignment:
    """After fix #4, public module-level assignments produce file::NAME chunks."""

    @pytest.fixture(autouse=True)
    def adapter(self):
        from codoc.lang.python import PythonAdapter

        self.adapter = PythonAdapter()

    def _chunks_by_sym(self, source: str, file: str = "mod.py") -> dict[str, str]:
        chunks = self.adapter.extract_chunks(file, source)
        return {c.symbol_path: c.source for c in chunks}

    def test_public_assignment_gets_named_chunk(self) -> None:
        src = "TIMEOUT = 30\n"
        syms = self._chunks_by_sym(src)
        assert "mod.py::TIMEOUT" in syms

    def test_private_assignment_stays_in_module_chunk(self) -> None:
        src = "_internal = 'hidden'\n"
        syms = self._chunks_by_sym(src)
        assert "mod.py::_internal" not in syms
        assert "mod.py::__module__" in syms

    def test_import_stays_in_module_chunk(self) -> None:
        src = "import os\n"
        syms = self._chunks_by_sym(src)
        assert "mod.py::os" not in syms
        assert "mod.py::__module__" in syms

    def test_function_definition_unaffected(self) -> None:
        src = "def foo():\n    pass\n"
        syms = self._chunks_by_sym(src)
        assert "mod.py::foo" in syms

    def test_mixed_module(self) -> None:
        src = (
            "import os\n"
            "DEBUG = True\n"
            "_hidden = 42\n"
            "ITEMS = [1, 2, 3]\n"
            "def run():\n    pass\n"
        )
        syms = self._chunks_by_sym(src)
        assert "mod.py::DEBUG" in syms
        assert "mod.py::ITEMS" in syms
        assert "mod.py::run" in syms
        assert "mod.py::_hidden" not in syms
        # Imports and _hidden land in __module__
        assert "mod.py::__module__" in syms


# ---------------------------------------------------------------------------
# TypeScript adapter: simple named declarations emit file::NAME chunks
# ---------------------------------------------------------------------------


class TestTypeScriptAdapterNamedVariable:
    """After fix #4, simple public const/let declarations emit file::NAME chunks."""

    @pytest.fixture(autouse=True)
    def adapter(self):
        from codoc.lang.typescript import TypeScriptAdapter

        self.adapter = TypeScriptAdapter()

    def _chunks_by_sym(self, source: str, file: str = "mod.ts") -> dict[str, str]:
        chunks = self.adapter.extract_chunks(file, source)
        return {c.symbol_path: c.source for c in chunks}

    def test_simple_const_gets_named_chunk(self) -> None:
        src = "const TIMEOUT = 30;\n"
        syms = self._chunks_by_sym(src)
        assert "mod.ts::TIMEOUT" in syms

    def test_private_const_stays_in_module_chunk(self) -> None:
        src = "const _internal = 'hidden';\n"
        syms = self._chunks_by_sym(src)
        assert "mod.ts::_internal" not in syms
        assert "mod.ts::__module__" in syms

    def test_arrow_function_still_gets_named_chunk(self) -> None:
        src = "const greet = (name: string) => `Hello ${name}`;\n"
        syms = self._chunks_by_sym(src)
        assert "mod.ts::greet" in syms

    def test_function_declaration_unaffected(self) -> None:
        src = "function foo(): void {}\n"
        syms = self._chunks_by_sym(src)
        assert "mod.ts::foo" in syms
