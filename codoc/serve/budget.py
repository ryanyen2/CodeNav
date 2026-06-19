"""budget.py — Denial-of-Wallet guardrails for remote-triggered realization (U8).

A remote suggestion can ultimately drive the local agent to spend API budget, so
each realization runs under a guard: a per-session cost cap, a tool-call cap, a
circuit breaker (open after consecutive failures), and a liveness timeout (a hung
run is not left spinning). Pure + tested; the realize loop consults it per step.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BudgetGuard:
    max_cost: float
    max_tool_calls: int
    breaker_threshold: int = 3
    liveness_timeout_s: float = 600.0

    _cost: float = 0.0
    _calls: int = 0
    _consecutive_failures: int = 0
    _open: bool = False

    def charge(self, cost: float) -> bool:
        """Add cost; returns True while still within the cap."""
        self._cost += max(0.0, cost)
        return self._cost <= self.max_cost

    def allow_tool_call(self) -> bool:
        """Consume one tool-call budget unit; False when capped or the breaker is open."""
        if self._open:
            return False
        self._calls += 1
        return self._calls <= self.max_tool_calls

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.breaker_threshold:
            self._open = True

    def record_success(self) -> None:
        self._consecutive_failures = 0

    def breaker_open(self) -> bool:
        return self._open

    def expired(self, started_at: float, now: float) -> bool:
        """True when the run has exceeded the liveness timeout (a hung realize)."""
        return (now - started_at) > self.liveness_timeout_s

    def tripped(self) -> bool:
        """True when any limit has been crossed — the realize loop should halt."""
        return (self._open
                or self._cost > self.max_cost
                or self._calls > self.max_tool_calls)

    @property
    def spent(self) -> float:
        return self._cost

    @property
    def calls(self) -> int:
        return self._calls
