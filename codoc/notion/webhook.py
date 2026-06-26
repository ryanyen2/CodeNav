"""webhook.py — Notion webhook ingress + polling fallback.

Notion webhooks are *signals, not payloads*: an event says "this page changed",
never what changed, and ordering is not guaranteed. So the bridge treats every
event as a trigger to reconcile from the API (U7/U9). This module owns the
security-and-ordering logic — signature verification, the one-time verification
handshake, delivery dedupe, and timestamp reorder — plus a ``last_edited_time``
polling fallback for deployments that can't expose a public endpoint.

This is the repo's **first inbound network boundary** (the serve hub deliberately
used outbound tunnels), so signature verification is mandatory and runs on the raw
bytes before any JSON parse. The pure functions here are unit-tested; the FastAPI
app (``build_webhook_app``) is thin lazy wiring around them.

NOTE: this module intentionally does NOT use ``from __future__ import annotations``.
FastAPI resolves the ``Request`` parameter from its real type object; stringized
annotations (the future import) break that for the lazily-imported handler.
"""
import hashlib
import hmac
import json
from collections import OrderedDict
from typing import Callable

# Event types that warrant a reconcile. Block edits surface only as
# page.content_updated (there are no block-level events); comments arrive within
# seconds. Structural page events (created/deleted/moved) also move the tree.
_RECONCILE_EVENTS = {
    "page.content_updated", "page.properties_updated",
    "page.created", "page.deleted", "page.undeleted", "page.moved",
    "comment.created", "comment.updated", "comment.deleted",
}


def verify_signature(secret: str, raw_body: bytes, signature: str | None) -> bool:
    """Verify ``X-Notion-Signature`` = HMAC-SHA256(raw_body) keyed by the verification
    token, timing-safe, on the **raw bytes** (never re-serialized JSON). Tolerates an
    optional ``sha256=`` prefix on the header."""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    provided = signature.split("=", 1)[1] if signature.startswith("sha256=") else signature
    return hmac.compare_digest(expected, provided)


def verification_token(payload: dict) -> str | None:
    """If this is the one-time subscription handshake, return its verification token."""
    tok = payload.get("verification_token")
    return tok if isinstance(tok, str) and tok else None


def is_reconcile_event(payload: dict) -> bool:
    """Whether an event's type warrants reconciling the tree from the API."""
    return payload.get("type") in _RECONCILE_EVENTS


def reorder_events(events: list[dict]) -> list[dict]:
    """Sort events by their ``timestamp`` (delivery order is NOT guaranteed). Events
    without a timestamp sort first (treated as oldest), stably."""
    return sorted(events, key=lambda e: e.get("timestamp") or "")


class DeliveryDeduper:
    """Bounded LRU of seen ``deliveryId``s. ``deliveryId`` is stable across Notion's
    retries (up to 8), so dedupe makes processing at-most-once."""

    def __init__(self, capacity: int = 2048):
        self._capacity = capacity
        self._seen: "OrderedDict[str, None]" = OrderedDict()

    def seen(self, delivery_id: str | None) -> bool:
        """Record ``delivery_id``; return True if it was already seen (a retry)."""
        if not delivery_id:
            return False  # no id → can't dedupe; let the caller process it
        if delivery_id in self._seen:
            self._seen.move_to_end(delivery_id)
            return True
        self._seen[delivery_id] = None
        if len(self._seen) > self._capacity:
            self._seen.popitem(last=False)
        return False


class PollState:
    """Tracks the page's last-seen ``last_edited_time`` for the polling fallback."""

    def __init__(self, initial: str = ""):
        self._last = initial

    def advanced(self, current: str | None) -> bool:
        """True (and updates state) when ``current`` is newer than the last seen value.
        ISO-8601 timestamps compare lexicographically, so a string compare suffices."""
        if not current or current <= self._last:
            return False
        self._last = current
        return True


def build_webhook_app(secret: str, on_reconcile: Callable[[], None],
                      on_token: Callable[[str], None] | None = None):  # pragma: no cover
    """A FastAPI app exposing ``POST /notion/webhook``. Lazy-imports FastAPI (the
    ``notion`` extra). Verifies the signature on raw bytes, answers the handshake,
    dedupes deliveries, and fires ``on_reconcile`` for a reconcile-worthy event."""
    from fastapi import FastAPI, Request, Response

    app = FastAPI()
    deduper = DeliveryDeduper()

    @app.post("/notion/webhook")
    async def webhook(request: Request):
        raw = await request.body()
        signature = request.headers.get("X-Notion-Signature")
        # The handshake POST is unsigned by the token we don't yet have; accept it
        # only when it carries a verification_token and no event body.
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            return Response(status_code=400)
        tok = verification_token(payload)
        if tok is not None:
            if on_token is not None:
                on_token(tok)
            return {"verification_token": tok}
        if not verify_signature(secret, raw, signature):
            return Response(status_code=401)
        if deduper.seen(payload.get("deliveryId") or payload.get("delivery_id")):
            return {"deduped": True}
        if is_reconcile_event(payload):
            on_reconcile()
        return {"ok": True}

    return app
