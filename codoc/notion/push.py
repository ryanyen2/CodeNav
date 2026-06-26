"""push.py — the host→Notion live-catch-up channel, dedup'd (the echo-loop guard).

The bridge re-renders the whole block tree from the store each time ``.codoc/*``
moves; this suppresses a push when the rendered blocks are byte-identical to the
last push. That is the echo-loop guard (R8/R10): a Notion edit lands in the store,
the store re-renders, and WITHOUT this guard the identical re-render would push
back to Notion and be re-detected as a fresh edit. Because the parse side is
idempotent under ``normalize_description``, a genuine no-op render produces zero
user ops on re-parse and zero bytes of change here, so the loop converges.

Dedup is on rendered CONTENT, not on the version: ``payload_version`` (the
restart-safe HLC stamp) advances every loop pass even when nothing changed, so it
cannot be the dedup key. It rides alongside a real push so a client/state can drop
a stale write after a restart, mirroring ``serve/push.py``.

``BlockPushStream`` is synchronous + unit-testable; the bridge wires it to a
``watchfiles`` loop (U9), exactly as ``serve.event_source`` wraps ``PayloadStream``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from codoc.serve.payload import payload_version


class BlockPushStream:
    """Yields the rendered block tree on first call, then only when it changes.

    ``render`` is a zero-arg callable returning the current Notion block list (the
    bridge passes ``lambda: render_blocks(store, fid_to_block=...)``). Injecting the
    renderer keeps this core free of store/Notion wiring and trivially testable.
    """

    def __init__(self, render: Callable[[], list[dict]]):
        self._render = render
        self._last: str | None = None

    def next_if_changed(self) -> list[dict] | None:
        blocks = self._render()
        serialized = json.dumps(blocks, sort_keys=True)
        if serialized == self._last:
            return None
        self._last = serialized
        return blocks


def current_version(codoc_dir: str | Path) -> int:
    """Restart-safe monotonic version for a push, derived from the daemon's HLC
    stamp on ``status.json`` (reused from the hub — never a per-process counter)."""
    return payload_version(codoc_dir)
