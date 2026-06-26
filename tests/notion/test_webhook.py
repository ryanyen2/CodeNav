"""U8 — webhook ingress logic + polling fallback (pure functions)."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from codoc.notion.webhook import (
    DeliveryDeduper, PollState, build_webhook_app, is_reconcile_event,
    reorder_events, verification_token, verify_signature,
)

_SECRET = "whsec_test"


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── signature verification ───────────────────────────────────────────────────

def test_valid_signature_accepted():
    body = b'{"type":"page.content_updated"}'
    assert verify_signature(_SECRET, body, _sign(_SECRET, body)) is True


def test_valid_signature_with_prefix():
    body = b'{"x":1}'
    assert verify_signature(_SECRET, body, "sha256=" + _sign(_SECRET, body)) is True


def test_tampered_body_rejected():
    body = b'{"type":"page.content_updated"}'
    sig = _sign(_SECRET, body)
    assert verify_signature(_SECRET, b'{"type":"evil"}', sig) is False


def test_wrong_key_rejected():
    body = b'{"x":1}'
    assert verify_signature(_SECRET, body, _sign("other", body)) is False


def test_missing_signature_or_secret_rejected():
    assert verify_signature(_SECRET, b"x", None) is False
    assert verify_signature("", b"x", "sig") is False


# ── handshake ────────────────────────────────────────────────────────────────

def test_verification_token_detected():
    assert verification_token({"verification_token": "tok-1"}) == "tok-1"
    assert verification_token({"type": "page.content_updated"}) is None


# ── reconcile-event classification ───────────────────────────────────────────

@pytest.mark.parametrize("etype,expected", [
    ("page.content_updated", True),
    ("comment.created", True),
    ("page.moved", True),
    ("page.locked", False),
    ("unknown.event", False),
])
def test_is_reconcile_event(etype, expected):
    assert is_reconcile_event({"type": etype}) is expected


# ── reorder ──────────────────────────────────────────────────────────────────

def test_reorder_events_by_timestamp():
    events = [{"id": "b", "timestamp": "2026-06-26T02:00:00Z"},
              {"id": "a", "timestamp": "2026-06-26T01:00:00Z"}]
    assert [e["id"] for e in reorder_events(events)] == ["a", "b"]


# ── dedupe ───────────────────────────────────────────────────────────────────

def test_deduper_detects_retry():
    d = DeliveryDeduper()
    assert d.seen("d-1") is False
    assert d.seen("d-1") is True   # retry
    assert d.seen("d-2") is False


def test_deduper_missing_id_not_deduped():
    assert DeliveryDeduper().seen(None) is False


def test_deduper_evicts_beyond_capacity():
    d = DeliveryDeduper(capacity=2)
    d.seen("a"); d.seen("b"); d.seen("c")  # evicts "a"
    assert d.seen("a") is False  # "a" was evicted → looks new again


# ── polling fallback ─────────────────────────────────────────────────────────

def test_poll_state_detects_advance():
    p = PollState("2026-06-26T01:00:00Z")
    assert p.advanced("2026-06-26T02:00:00Z") is True
    assert p.advanced("2026-06-26T02:00:00Z") is False  # same → no advance
    assert p.advanced("2026-06-26T01:30:00Z") is False  # older → no advance


# ── FastAPI wiring (optional: only when the extra is installed) ───────────────

def test_webhook_app_end_to_end():
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    fired = []
    app = build_webhook_app(_SECRET, on_reconcile=lambda: fired.append(1))
    client = TestClient(app)

    # handshake echoes the token
    r = client.post("/notion/webhook", json={"verification_token": "tok-9"})
    assert r.status_code == 200 and r.json()["verification_token"] == "tok-9"

    # a signed reconcile event fires the callback
    body = json.dumps({"type": "page.content_updated", "deliveryId": "d1"}).encode()
    r = client.post("/notion/webhook", content=body,
                    headers={"X-Notion-Signature": _sign(_SECRET, body)})
    assert r.status_code == 200 and fired == [1]

    # a forged signature is rejected
    r = client.post("/notion/webhook", content=body,
                    headers={"X-Notion-Signature": "deadbeef"})
    assert r.status_code == 401
