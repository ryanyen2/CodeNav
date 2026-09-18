"""Hybrid logical clock (spec §7.2).

An HLC string is ``"<wall ms:020d>-<logical:020d>-<node>"`` so lexicographic
order is the clock's total order in Python, SQLite and JavaScript alike. The
clock advances past every stamp it observes, which is what keeps an op typed
after a pull later than the characters it anchors to, whatever the machine's
wall clock says.

Only the applier stamps ``at``: a client submission carries none, and a log on
disk is the truth, so there is nothing to refuse at replay. A peer's fast clock
therefore moves this clock forward too; the order stays total and consistent,
which is the property the calculus needs.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class Clock:
    node: str
    wall: int = 0
    logical: int = 0

    def _now(self) -> int:
        return int(time.time() * 1000)

    def tick(self) -> str:
        now = self._now()
        if now > self.wall:
            self.wall, self.logical = now, 0
        else:
            self.logical += 1
        return fmt(self.wall, self.logical, self.node)

    def observe(self, stamp: str) -> None:
        """Advance past a stamp seen in a replayed log."""
        wall, logical, _ = parse(stamp)
        if wall > self.wall:
            self.wall, self.logical = wall, logical
        elif wall == self.wall and logical > self.logical:
            self.logical = logical


def fmt(wall: int, logical: int, node: str) -> str:
    return f"{wall:020d}-{logical:020d}-{node}"


def parse(stamp: str) -> tuple[int, int, str]:
    wall, logical, node = stamp.split("-", 2)
    return int(wall), int(logical), node


def is_valid(stamp: str) -> bool:
    try:
        parse(stamp)
        return True
    except (ValueError, AttributeError):
        return False
