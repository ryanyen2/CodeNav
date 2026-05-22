"""Tests for codoc.listener.debouncer — FileDebouncer."""
from __future__ import annotations

import asyncio

import pytest

from codoc.listener.debouncer import FileDebouncer, DEBOUNCE_SECONDS


@pytest.fixture
def db() -> FileDebouncer:
    return FileDebouncer()


@pytest.mark.asyncio
async def test_single_call_fires_once(db):
    """A single schedule fires the callback exactly once."""
    calls = []

    async def cb():
        calls.append(1)

    await db.schedule("key1", cb)
    await asyncio.sleep(DEBOUNCE_SECONDS + 0.2)
    assert calls == [1]


@pytest.mark.asyncio
async def test_rapid_calls_coalesce(db):
    """Five rapid schedules on the same key fire the callback once."""
    calls = []

    async def cb():
        calls.append(1)

    for _ in range(5):
        await db.schedule("key1", cb)
        await asyncio.sleep(0.05)

    await asyncio.sleep(DEBOUNCE_SECONDS + 0.3)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_different_keys_fire_independently(db):
    """Schedules on different keys each fire their own callback."""
    results = {}

    async def make_cb(name):
        async def cb():
            results[name] = True
        return cb

    await db.schedule("key1", await make_cb("a"))
    await db.schedule("key2", await make_cb("b"))
    await asyncio.sleep(DEBOUNCE_SECONDS + 0.3)
    assert results == {"a": True, "b": True}


@pytest.mark.asyncio
async def test_callback_exception_is_swallowed(db):
    """Exceptions in callbacks do not propagate."""
    async def bad_cb():
        raise ValueError("boom")

    await db.schedule("key1", bad_cb)
    # Should not raise after the debounce window.
    await asyncio.sleep(DEBOUNCE_SECONDS + 0.2)


@pytest.mark.asyncio
async def test_pending_cleared_after_fire(db):
    """After firing, the key is removed from _pending."""
    async def cb():
        pass

    await db.schedule("key1", cb)
    await asyncio.sleep(DEBOUNCE_SECONDS + 0.2)
    assert "key1" not in db._pending
