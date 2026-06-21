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

from pathlib import Path

_ACTIVE_STATES = frozenset({"awaiting_impl", "realizing"})
_DONE_FILENAME = "realize_done.json"


def ready_directives(status: dict, manifest: list[dict]) -> list[dict]:
    """The directives ready to realize: handed-off entries while the queue is
    awaiting implementation. Anything still held (``handed_off`` falsey) is skipped."""
    if not isinstance(status, dict) or status.get("state") not in _ACTIVE_STATES:
        return []
    return [d for d in manifest
            if isinstance(d, dict) and d.get("handed_off") and d.get("id")]


def filter_undone(directives: list[dict], done_ids) -> list[dict]:
    """Drop directives already realized — done-tracking keyed on directive id (U8),
    so a re-fire (a fresh trigger pass over the same manifest) never re-implements
    work already shipped as a PR."""
    done = set(done_ids or ())
    return [d for d in directives if d.get("id") not in done]


def _done_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / _DONE_FILENAME


def read_done(codoc_dir: str | Path) -> set[str]:
    from codoc.loop.fsio import read_json

    data = read_json(_done_path(codoc_dir), default={}) or {}
    return {str(d) for d in (data.get("done") or [])}


def mark_done(codoc_dir: str | Path, directive_id: str) -> None:
    from codoc.loop.fsio import atomic_write_json

    done = read_done(codoc_dir)
    done.add(directive_id)
    atomic_write_json(_done_path(codoc_dir), {"version": 1, "done": sorted(done)})
