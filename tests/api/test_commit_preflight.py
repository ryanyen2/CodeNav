"""Tests for POST /commit/preflight."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codoc.api.app import create_app
from codoc.storage.sqlite_store import SQLiteStore
from codoc.core.log import TransactionLog
from codoc.model.feature import Feature
from codoc.model.hlc import HLC
from codoc.model.transaction import Transaction, TransactionKind


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture
def codoc_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".codoc"
    d.mkdir()
    db = SQLiteStore(str(d / "codoc.db"))
    db.open()
    db.close()
    return d


def test_preflight_clean_no_proposals(client, codoc_dir):
    """When there are no pending proposals, blocked=False."""
    resp = client.post("/commit/preflight", json={
        "root_dir": str(codoc_dir.parent),
        "staged_files": ["src/auth.py"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["blocked"] is False
    assert data["pending"] == []


def test_preflight_blocked_with_pending_proposals(client, codoc_dir, tmp_path):
    """When there are pending proposals, blocked=True."""
    # Insert a pending proposal into the DB.
    db = SQLiteStore(str(codoc_dir / "codoc.db"))
    db.open()
    try:
        tx_log = TransactionLog(db, node_id="test")
        hlc = HLC.now(node_id="test")
        tx = Transaction(
            hlc=hlc,
            parent_hlcs=[],
            kind=TransactionKind.ABSORB,
            payload={"file": "src/auth.py", "symbol_path": "src/auth.py::login"},
            author="reflective",
            proposal=True,
        )
        tx_log.append_proposal(tx)
    finally:
        db.close()

    resp = client.post("/commit/preflight", json={
        "root_dir": str(codoc_dir.parent),
        "staged_files": ["src/auth.py"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["blocked"] is True
    assert len(data["pending"]) >= 1


def test_preflight_staged_files_not_overlapping(client, codoc_dir):
    """Pending proposals for unrelated files are still reported (conservative)."""
    db = SQLiteStore(str(codoc_dir / "codoc.db"))
    db.open()
    try:
        tx_log = TransactionLog(db, node_id="test")
        hlc = HLC.now(node_id="test")
        # Proposal with no "file" field in payload — conservative: always blocks.
        tx = Transaction(
            hlc=hlc,
            parent_hlcs=[],
            kind=TransactionKind.INTRODUCE,
            payload={"slug": "new-feature", "title": "New Feature", "intent": "..."},
            author="plan",
            proposal=True,
        )
        tx_log.append_proposal(tx)
    finally:
        db.close()

    resp = client.post("/commit/preflight", json={
        "root_dir": str(codoc_dir.parent),
        "staged_files": ["unrelated/file.py"],
    })
    data = resp.json()
    # INTRODUCE with no "file" field → conservative include → blocked.
    assert data["blocked"] is True


def test_preflight_empty_staged_files_not_blocked(client, codoc_dir):
    """No staged files → clean (nothing to check)."""
    resp = client.post("/commit/preflight", json={
        "root_dir": str(codoc_dir.parent),
        "staged_files": [],
    })
    data = resp.json()
    assert data["blocked"] is False
