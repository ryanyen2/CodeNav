"""Minimal shared fixtures for the rewritten test suite."""
from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def mosaic_dir() -> Path:
    return _REPO_ROOT / "test" / "mosaic"
