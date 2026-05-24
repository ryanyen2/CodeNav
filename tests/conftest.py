"""Shared pytest fixtures for codoc tests."""

from __future__ import annotations

import os

import uuid as _uuid
from pathlib import Path
from typing import Callable

import pytest

from codoc.core.log import TransactionLog
from codoc.model.anchor import Anchor
from codoc.model.binding import Binding
from codoc.model.feature import Feature
from codoc.model.hlc import HLC
from codoc.storage.jsonl_log import JSONLLog
from codoc.storage.sqlite_store import SQLiteStore


REPO_ROOT = Path(__file__).resolve().parent.parent
DRACO_DIR = REPO_ROOT / "test" / "draco"
MOSAIC_DIR = REPO_ROOT / "test" / "mosaic"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture
def tmp_store(tmp_path: Path) -> SQLiteStore:
    db_path = tmp_path / "codoc.db"
    store = SQLiteStore(str(db_path))
    store.open()
    yield store
    store.close()


@pytest.fixture
def tmp_jsonl_log(tmp_path: Path) -> JSONLLog:
    return JSONLLog(str(tmp_path / "log.jsonl"))


@pytest.fixture
def tmp_tx_log(tmp_store: SQLiteStore) -> TransactionLog:
    return TransactionLog(tmp_store, node_id="test")


@pytest.fixture
def hlc_now() -> HLC:
    return HLC.now(node_id="test")


@pytest.fixture
def make_feature() -> Callable[..., Feature]:
    def _make(
        slug: str = "feature-1",
        intent: str = "Test feature intent.",
        parent_uuid: str | None = None,
        retired: bool = False,
        uuid: str | None = None,
        hlc: HLC | None = None,
    ) -> Feature:
        u = uuid or str(_uuid.uuid4())
        h = hlc or HLC.now(node_id="test")
        return Feature(
            uuid=u,
            slug=slug,
            parent_uuid=parent_uuid,
            intent=intent,
            retired=retired,
            created_at_hlc=h,
            updated_at_hlc=h,
        )

    return _make


@pytest.fixture
def make_binding() -> Callable[..., Binding]:
    def _make(
        feature_uuid: str,
        file: str = "pkg/file.py",
        symbol_path: str | None = "pkg/file.py::func",
        ts_query: str | None = None,
        fingerprint: str = "0" * 64,
        uuid: str | None = None,
        hlc: HLC | None = None,
        parent_symbol: str | None = None,
    ) -> Binding:
        u = uuid or str(_uuid.uuid4())
        h = hlc or HLC.now(node_id="test")
        anchor = Anchor(
            file=file,
            symbol_path=symbol_path,
            ts_query=ts_query,
        )
        return Binding(
            uuid=u,
            feature_uuid=feature_uuid,
            anchor=anchor,
            fingerprint=fingerprint,
            fingerprint_at_hlc=h,
            parent_symbol=parent_symbol,
        )

    return _make


@pytest.fixture
def draco_dir() -> Path:
    return DRACO_DIR


@pytest.fixture
def mosaic_dir() -> Path:
    return MOSAIC_DIR


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR
