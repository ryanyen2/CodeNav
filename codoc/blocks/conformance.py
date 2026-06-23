"""Host-conformance harness (U8) — the canonical block view every host must match.

The "many surfaces" claim only holds if every host derives the *same* blocks from
the same protocol. This is the single source of truth for what a conforming host
renders, modeled on the existing TS↔``parse.py`` parity-test pattern: a fixture
sidecar (``tests/fixtures/blocks_conformance.json``) is read by each host, and
each must reproduce :func:`canonical_block_view`. A host that drops a block,
mistypes a ``kind``/``lifecycle``, or reorders is caught by the diff.

Kept deliberately small and pure so the TS reader can mirror it line-for-line.
"""
from __future__ import annotations


def canonical_block_view(sidecar: dict) -> dict[str, list[dict]]:
    """The normalized per-feature block view a conforming host must render: blocks
    ordered by ``ord``, each carrying ``id``/``kind``/``content``/``lifecycle``/
    ``provenance``/``ord``. Tolerant of a v5 sidecar (no ``blocks`` → empty)."""
    raw = sidecar.get("blocks") or {}
    out: dict[str, list[dict]] = {}
    for fid, entries in raw.items():
        view = [
            {
                "id": e.get("id", ""),
                "kind": e.get("kind", ""),
                "content": e.get("content", ""),
                "lifecycle": e.get("lifecycle", "persistent"),
                "provenance": e.get("provenance", "human"),
                "ord": int(e.get("ord", 0)),
            }
            for e in (entries or [])
        ]
        view.sort(key=lambda b: b["ord"])
        out[fid] = view
    return out
