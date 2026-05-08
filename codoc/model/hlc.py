import time

from pydantic import BaseModel


class HLC(BaseModel):
    logical_time: int = 0
    wall_clock: int = 0  # unix milliseconds
    node_id: str = "default"

    model_config = {"frozen": True}

    def _tuple(self) -> tuple[int, int, str]:
        # wall_clock is the primary sort key; logical_time breaks ties within the same millisecond.
        return (self.wall_clock, self.logical_time, self.node_id)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, HLC):
            return NotImplemented
        return self._tuple() < other._tuple()

    def __le__(self, other: object) -> bool:
        if not isinstance(other, HLC):
            return NotImplemented
        return self._tuple() <= other._tuple()

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, HLC):
            return NotImplemented
        return self._tuple() > other._tuple()

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, HLC):
            return NotImplemented
        return self._tuple() >= other._tuple()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HLC):
            return NotImplemented
        return self._tuple() == other._tuple()

    def __hash__(self) -> int:
        return hash(self._tuple())

    @classmethod
    def now(cls, node_id: str = "default") -> "HLC":
        wall_ms = int(time.time() * 1000)
        return cls(logical_time=0, wall_clock=wall_ms, node_id=node_id)

    def advance(self, observed: "HLC | None" = None) -> "HLC":
        wall_ms = int(time.time() * 1000)
        if observed is None:
            new_wall = max(self.wall_clock, wall_ms)
            if new_wall == self.wall_clock:
                # Wall clock didn't advance; bump logical to maintain monotonicity.
                return HLC(logical_time=self.logical_time + 1, wall_clock=new_wall, node_id=self.node_id)
            return HLC(logical_time=0, wall_clock=new_wall, node_id=self.node_id)
        else:
            new_wall = max(self.wall_clock, observed.wall_clock, wall_ms)
            if new_wall == self.wall_clock and new_wall == observed.wall_clock:
                new_logical = max(self.logical_time, observed.logical_time) + 1
            elif new_wall == self.wall_clock:
                new_logical = self.logical_time + 1
            elif new_wall == observed.wall_clock:
                new_logical = observed.logical_time + 1
            else:
                new_logical = 0
            return HLC(logical_time=new_logical, wall_clock=new_wall, node_id=self.node_id)

    def to_str(self) -> str:
        # Lexicographic string order matches the total order: wall_clock → logical_time → node_id.
        return f"{self.wall_clock:020d}-{self.logical_time:020d}-{self.node_id}"

    @classmethod
    def from_str(cls, s: str) -> "HLC":
        parts = s.split("-", 2)
        if len(parts) != 3:
            raise ValueError(f"Invalid HLC string: {s!r}")
        wall_clock, logical_time, node_id = parts
        return cls(logical_time=int(logical_time), wall_clock=int(wall_clock), node_id=node_id)
