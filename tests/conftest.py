"""Shared pytest fixtures."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# The local person is `human:<git config user.email>` (workspace/log.py). With
# the developer's global git config hidden, a test's person is the fallback
# `human:local` unless the test gives its repository an email of its own.
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"
