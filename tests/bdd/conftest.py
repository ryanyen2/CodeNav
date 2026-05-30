"""Fixtures for the BDD scenario suite."""
from __future__ import annotations

import pytest

from tests.bdd.world import World


@pytest.fixture
def world(tmp_path) -> World:
    """A fresh codoc workspace (real repo dir + real .codoc store) under test."""
    root = tmp_path / "repo"
    root.mkdir()
    codoc_dir = root / ".codoc"
    codoc_dir.mkdir()
    return World(root, codoc_dir)
