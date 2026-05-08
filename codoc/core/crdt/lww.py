"""
codoc.core.crdt.lww — Last-Write-Wins register backed by a pycrdt Y.Map slot.

Concurrency semantics
---------------------
- incoming HLC > current HLC  → overwrite, clear any existing conflict.
- incoming HLC == current HLC, same node_id  → idempotent no-op.
- incoming HLC == current HLC, different node_id  → concurrent write;
  record conflict_value / conflict_hlc; surface to caller via has_conflict=True.
- incoming HLC < current HLC  → discard (stale update).

Y.Map stores a JSON-serialised dict under `slot` with the following keys:
    "value"          – JSON-serialised current winning value (may be None)
    "hlc"            – HLC.to_str() of the current write
    "conflict_value" – JSON-serialised conflicting value (absent or null when no conflict)
    "conflict_hlc"   – HLC.to_str() of the conflicting write (absent or null when no conflict)

All mutations happen inside Y.Map transactions so remote peers receive
a single CRDT update per logical operation.
"""

from __future__ import annotations

import json

import pycrdt

from codoc.model.hlc import HLC

_SENTINEL_HLC = HLC(logical_time=0, wall_clock=0, node_id="\x00")


class LWWRegister:
    """Last-Write-Wins register occupying a single slot inside a pycrdt Y.Map.

    Parameters
    ----------
    ymap:
        The parent Y.Map that owns this register.  The register stores its
        state as a nested Y.Map under *slot*.
    slot:
        Key inside *ymap* that this register owns.
    """

    def __init__(self, ymap: pycrdt.Map, slot: str) -> None:
        self._ymap = ymap
        self._slot = slot
        # Lazily initialise the slot if it does not exist yet.
        if slot not in ymap:
            inner: pycrdt.Map = pycrdt.Map()
            ymap[slot] = inner

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _inner(self) -> pycrdt.Map:
        return self._ymap[self._slot]  # type: ignore[return-value]

    def _current_hlc(self) -> HLC:
        inner = self._inner()
        raw = inner.get("hlc")
        if raw is None:
            return _SENTINEL_HLC
        return HLC.from_str(str(raw))

    def _conflict_hlc(self) -> HLC | None:
        inner = self._inner()
        raw = inner.get("conflict_hlc")
        if raw is None:
            return None
        return HLC.from_str(str(raw))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def _logical_wall(h: HLC) -> tuple[int, int]:
        """Compare key: (logical_time, wall_clock) — excludes node_id.

        Two HLCs are *concurrent* when they share the same logical_time and
        wall_clock but have different node_ids.  Node_id is used only as a
        deterministic tie-break for convergence, not for causal ordering.
        """
        return (h.logical_time, h.wall_clock)

    def set(self, value: object, hlc: HLC) -> None:
        """Write *value* at logical time *hlc*.

        The method implements LWW with conflict detection for concurrent writes
        from different nodes.

        Concurrency is defined as: same (logical_time, wall_clock) but
        different node_id.  In that case the lex-higher node_id wins for
        convergence, and the losing value is stored in the conflict slot so
        it can be surfaced to the user.
        """
        current_hlc = self._current_hlc()
        inner = self._inner()

        incoming_key = self._logical_wall(hlc)
        current_key = self._logical_wall(current_hlc)

        if incoming_key > current_key:
            # Clearly newer write: overwrite and clear any stale conflict.
            inner["value"] = json.dumps(value)
            inner["hlc"] = hlc.to_str()
            inner["conflict_value"] = json.dumps(None)
            inner["conflict_hlc"] = json.dumps(None)

        elif incoming_key == current_key:
            # Same causal position — check node_id to distinguish idempotent
            # replay from a genuine concurrent write.
            if hlc.node_id == current_hlc.node_id:
                # Idempotent replay from the same node — no-op.
                return

            # Different nodes, same (logical_time, wall_clock): concurrent write.
            current_raw = inner.get("value")
            current_value = json.loads(str(current_raw)) if current_raw is not None else None
            incoming_value = value

            if current_value == incoming_value:
                # Same value written concurrently — no visible conflict.
                return

            # Record conflict: the lex-higher node_id becomes the "winning"
            # value so that all peers converge to the same displayed value.
            if hlc.node_id > current_hlc.node_id:
                # Incoming wins the tie-break; existing becomes conflict slot.
                inner["conflict_value"] = inner.get("value", json.dumps(None))
                inner["conflict_hlc"] = inner.get("hlc", json.dumps(None))
                inner["value"] = json.dumps(incoming_value)
                inner["hlc"] = hlc.to_str()
            else:
                # Current wins the tie-break; incoming goes into conflict slot.
                inner["conflict_value"] = json.dumps(incoming_value)
                inner["conflict_hlc"] = hlc.to_str()
        # else incoming_key < current_key: stale — discard silently.

    def get(self) -> tuple[object, bool]:
        """Return ``(value, has_conflict)``.

        *value* is the currently winning value (highest HLC, or lex-highest
        node_id on a tie).  *has_conflict* is ``True`` when a concurrent write
        from a different node produced a diverging value that still needs
        explicit resolution.
        """
        inner = self._inner()
        raw_value = inner.get("value")
        value = json.loads(str(raw_value)) if raw_value is not None else None

        raw_conflict = inner.get("conflict_value")
        conflict = json.loads(str(raw_conflict)) if raw_conflict is not None else None
        has_conflict = conflict is not None

        return value, has_conflict

    def resolve_conflict(self, chosen_value: object) -> None:
        """Explicitly resolve a conflict by picking *chosen_value*.

        Clears the conflict slots and sets *value* to the caller's choice.
        This should be called after surfacing the conflict to the user and
        receiving their decision; in the full protocol this is wrapped in a
        RESTRUCTURE transaction with a fresh HLC.
        """
        inner = self._inner()
        inner["value"] = json.dumps(chosen_value)
        inner["conflict_value"] = json.dumps(None)
        inner["conflict_hlc"] = json.dumps(None)
