"""push.py — the SSE live channel host→browser (U3).

The daemon re-renders whole files every pass (there is no delta primitive), so
the hub pushes a full, version-guarded payload rather than deltas: a snapshot on
connect, then a re-push whenever ``.codoc/*`` changes AND the derived payload
actually differs from the last one sent. The differ is the broadcast-storm guard
(KTD8): a no-op re-render — the same bytes — produces no event, so a normalization
delta cannot fan out to every connected browser.

``PayloadStream`` holds the dedup state and is synchronous + unit-testable; the
async SSE generator (``event_source``) is the thin watchfiles wiring around it.
"""
from __future__ import annotations

import json
from pathlib import Path

from codoc.serve.payload import build_browser_payload


class PayloadStream:
    """Yields the browser payload on first call, then only when it changes.

    ``next_if_changed`` returns the current payload the first time (cold browser
    needs the snapshot) and thereafter only when the serialized payload differs
    from the last one emitted — so identical re-renders are suppressed."""

    def __init__(self, codoc_dir: str | Path):
        self._codoc_dir = Path(codoc_dir)
        self._last: str | None = None

    def next_if_changed(self) -> dict | None:
        payload = build_browser_payload(self._codoc_dir)
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        if serialized == self._last:
            return None
        self._last = serialized
        return payload


async def event_source(codoc_dir: str | Path, *, is_disconnected=None, viewer=None):
    """Async generator of SSE ``{"data": <json>}`` events.

    Emits the snapshot immediately, then re-pushes on every ``.codoc`` change that
    moves the payload. ``is_disconnected`` (an async predicate, e.g. Starlette's
    ``request.is_disconnected``) lets the stream stop when the client drops, so a
    closed browser doesn't leak the watch task.

    ``viewer`` is THIS connection's capability block (payload.viewer_block). It is
    merged into each payload on the way out rather than computed inside the shared
    ``PayloadStream``, which caches one payload for every connected viewer — baking
    a capability in there would serve one viewer's authority to all of them."""
    from watchfiles import awatch

    def _emit(payload: dict) -> str:
        return json.dumps({**payload, "viewer": viewer} if viewer else payload,
                          ensure_ascii=False)

    stream = PayloadStream(codoc_dir)
    first = stream.next_if_changed()
    yield {"data": _emit(first)}

    async for _changes in awatch(str(codoc_dir)):
        if is_disconnected is not None and await is_disconnected():
            return
        payload = stream.next_if_changed()
        if payload is not None:
            yield {"data": _emit(payload)}
