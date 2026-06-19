"""ratelimit.py — per-identity request rate limiting for the hub (U6).

A remote user (or a misconfiguration window past the Access edge) must not be
able to flood the write/SSE endpoints — each write wakes the daemon and fans out
to every connected browser, so an unbounded request rate is both a daemon DoS and
a broadcast amplifier. This is a small, dependency-free token bucket keyed per
GitHub identity; the route rejects with HTTP 429 when a key runs dry.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class _Bucket:
    tokens: float
    last: float


class RateLimiter:
    """A per-key token bucket.

    Each key (a GitHub login, or ``"anon"``) gets ``capacity`` tokens that refill at
    ``refill_per_sec``. ``allow(key)`` consumes one token and returns True; when the
    bucket is empty it returns False. A burst up to ``capacity`` is permitted; the
    sustained rate is bounded by ``refill_per_sec``. ``clock`` is injectable for tests."""

    def __init__(
        self,
        capacity: float,
        refill_per_sec: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        if capacity <= 0 or refill_per_sec <= 0:
            raise ValueError("capacity and refill_per_sec must be positive")
        self.capacity = float(capacity)
        self.refill = float(refill_per_sec)
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, key: str) -> bool:
        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self.capacity, last=now)
            self._buckets[key] = bucket
        elapsed = max(0.0, now - bucket.last)
        bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.refill)
        bucket.last = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True
        return False
