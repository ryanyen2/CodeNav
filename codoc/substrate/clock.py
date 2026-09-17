"""Hybrid logical clock (spec §7.2).

An HLC string is ``"<wall ms:020d>-<logical:020d>-<node>"`` so lexicographic
order is the clock's total order in Python, SQLite and JavaScript alike. The
clock advances past every stamp it observes, which is what keeps an op typed
after a pull later than the characters it anchors to, whatever the machine's
wall clock says. A stamp more than ``MAX_SKEW_MS`` ahead of the local wall
clock is refused by ``observe`` so one wrong clock cannot drag every later
stamp into the future.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

MAX_SKEW_MS = 10 * 60 * 1000


class SkewError(ValueError):
    """A stamp too far ahead of this machine's wall clock to be trusted."""


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
        """Advance past a stamp seen in a replayed log. Raises ``SkewError`` when
        the stamp is implausibly far in the future."""
        wall, logical, _ = parse(stamp)
        if wall - self._now() > MAX_SKEW_MS:
            raise SkewError(stamp)
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
