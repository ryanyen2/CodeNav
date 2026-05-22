from __future__ import annotations
import asyncio
import time
from typing import Callable, Awaitable

DEBOUNCE_SECONDS = 0.75

class FileDebouncer:
    """Per-file debouncer: coalesces rapid edits into a single reflect call."""

    def __init__(self):
        self._pending: dict[str, asyncio.TimerHandle] = {}
        self._lock = asyncio.Lock()

    async def schedule(
        self,
        file_key: str,   # e.g. "root_dir::rel_path"
        callback: Callable[[], Awaitable[None]],
    ) -> None:
        """Schedule callback to run after DEBOUNCE_SECONDS of silence on file_key."""
        async with self._lock:
            existing = self._pending.get(file_key)
            if existing:
                existing.cancel()
            loop = asyncio.get_event_loop()
            handle = loop.call_later(
                DEBOUNCE_SECONDS,
                lambda: asyncio.ensure_future(self._fire(file_key, callback)),
            )
            self._pending[file_key] = handle

    async def _fire(self, file_key: str, callback: Callable[[], Awaitable[None]]) -> None:
        async with self._lock:
            self._pending.pop(file_key, None)
        try:
            await callback()
        except Exception:
            pass  # reflect errors are non-fatal for the debouncer

# Module-level singleton
debouncer = FileDebouncer()
