"""realize_trigger.py — decide which directives the hub realizes (U7).

The deployed hub has no interactive session, so the daemon's ``--auto-realize``
fallback is disabled (KTD7) and the SERVER owns the realize trigger: it watches
``status.json`` + the ``realize.json`` manifest and fires only on directives that
have been **handed off** — an authorized hand-off cleared their draft, so Loop B
marked them ``handed_off``. Held drafts are excluded: the suggestion→execution
crossing happens only here, on an explicit hand-off. The watch loop is the thin
live wiring; ``ready_directives`` is the pure, tested decision.
"""
from __future__ import annotations

_ACTIVE_STATES = frozenset({"awaiting_impl", "realizing"})


def ready_directives(status: dict, manifest: list[dict]) -> list[dict]:
    """The directives ready to realize: handed-off entries while the queue is
    awaiting implementation. Anything still held (``handed_off`` falsey) is skipped."""
    if not isinstance(status, dict) or status.get("state") not in _ACTIVE_STATES:
        return []
    return [d for d in manifest
            if isinstance(d, dict) and d.get("handed_off") and d.get("id")]
