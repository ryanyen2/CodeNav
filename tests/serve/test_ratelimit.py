"""U6 — the per-identity token-bucket rate limiter."""
from __future__ import annotations

import pytest

from codoc.serve.ratelimit import RateLimiter


def test_burst_up_to_capacity_then_blocked():
    clock = {"t": 0.0}
    rl = RateLimiter(capacity=3, refill_per_sec=1, clock=lambda: clock["t"])
    assert [rl.allow("u") for _ in range(3)] == [True, True, True]
    assert rl.allow("u") is False  # bucket empty


def test_refills_over_time():
    clock = {"t": 0.0}
    rl = RateLimiter(capacity=2, refill_per_sec=1, clock=lambda: clock["t"])
    assert rl.allow("u") and rl.allow("u")
    assert rl.allow("u") is False
    clock["t"] = 1.0  # +1s → +1 token
    assert rl.allow("u") is True
    assert rl.allow("u") is False


def test_per_key_isolation():
    clock = {"t": 0.0}
    rl = RateLimiter(capacity=1, refill_per_sec=1, clock=lambda: clock["t"])
    assert rl.allow("maya") is True
    assert rl.allow("maya") is False
    assert rl.allow("ryan") is True  # a different identity has its own bucket


def test_capacity_caps_burst_even_after_idle():
    clock = {"t": 0.0}
    rl = RateLimiter(capacity=2, refill_per_sec=1, clock=lambda: clock["t"])
    clock["t"] = 100.0  # idle a long time
    assert rl.allow("u") and rl.allow("u")
    assert rl.allow("u") is False  # refill never exceeds capacity


def test_invalid_params_rejected():
    with pytest.raises(ValueError):
        RateLimiter(capacity=0, refill_per_sec=1)
    with pytest.raises(ValueError):
        RateLimiter(capacity=1, refill_per_sec=0)
