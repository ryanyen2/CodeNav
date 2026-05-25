"""Shared fixtures for graph tests."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from codoc.store.db import open_store


@dataclass
class FakeRow:
    """Minimal ChunkRow stand-in for graph tests (no LanceDB needed)."""

    file: str
    symbol_path: str
    source: str = ""
    language: str = "python"
    id: int = 0
    tokens_hash: str = "h"
    types_hash: str = "t"
    minhash: bytes = field(default=b"")
    start_byte: int = 0
    end_byte: int = 0
    embedding: object = None


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()
