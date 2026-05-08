"""
codoc.core.crdt.aw_map — Add-Wins map keyed by string UUID, backed by pycrdt Y.Map.

Semantics
---------
- Each UUID key maps to a nested Y.Map with:
      "value"           – JSON-serialised payload
      "tombstoned"      – JSON bool; True means the entry has been soft-deleted
      "concurrent_add"  – JSON bool; True when concurrent adds from different
                           nodes produced different values for the same UUID
                           (rare; UUIDs should be unique across nodes)

- **Add wins over concurrent remove**: because UUIDs (UUIDv7) are minted fresh
  per add, a concurrent add will always use a key not yet observed by the
  concurrent remove.  For the same UUID key, an add that arrives after a
  tombstone was set wins by clearing the tombstone — this models Observed-Remove
  semantics: the remove only cancelled the add-tags it had observed.

- Tombstones persist forever; UUIDs are never reused.

- `add` is idempotent for the same (uuid, value) pair.

All reads are non-transactional (fine for CRDT maps); writes rely on Y.Map's
built-in LWW per-key semantics for convergence.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pycrdt


class AWMap:
    """Add-Wins map backed by a pycrdt Y.Map.

    Parameters
    ----------
    ymap:
        The Y.Map that acts as the backing store.  Each UUID key inside it
        holds a nested Y.Map with ``value``, ``tombstoned``, and
        ``concurrent_add`` fields.
    """

    def __init__(self, ymap: pycrdt.Map) -> None:
        self._ymap = ymap

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _entry(self, uuid: str) -> pycrdt.Map | None:
        entry = self._ymap.get(uuid)
        if entry is None:
            return None
        return entry  # type: ignore[return-value]

    def _get_or_create_entry(self, uuid: str) -> pycrdt.Map:
        entry = self._ymap.get(uuid)
        if entry is None:
            inner: pycrdt.Map = pycrdt.Map()
            self._ymap[uuid] = inner
            return inner
        return entry  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, uuid: str, value: dict) -> None:
        """Insert or re-activate *uuid* with *value*.

        - Fresh UUID: creates the entry, tombstoned=False.
        - Re-add of a tombstoned entry: un-tombstones and updates the value
          (add wins over a concurrent or prior remove; the new value is taken
          as authoritative because the caller minted this UUID fresh or is
          deliberately reviving it).
        - Idempotent replay (same UUID, same value, not tombstoned): clears
          tombstone and returns unchanged.
        - Concurrent add collision (same UUID, different value, not tombstoned):
          marks ``concurrent_add=True`` and keeps the existing value canonical
          so the collision is visible for inspection.
        """
        inner = self._get_or_create_entry(uuid)

        tombstoned_raw = inner.get("tombstoned")
        is_tombstoned = tombstoned_raw is not None and json.loads(str(tombstoned_raw))

        existing_raw = inner.get("value")

        if is_tombstoned:
            # Re-add after remove: add wins; update value and clear tombstone.
            inner["value"] = json.dumps(value)
            inner["tombstoned"] = json.dumps(False)
            inner["concurrent_add"] = json.dumps(False)
            return

        if existing_raw is not None:
            existing_value = json.loads(str(existing_raw))

            if existing_value == value:
                # Idempotent replay — ensure tombstone is clear and return.
                inner["tombstoned"] = json.dumps(False)
                return

            # Different values for the same non-tombstoned UUID: concurrent add
            # collision (theoretically impossible with UUIDv7 minting, but
            # handled defensively).  Keep existing canonical; flag for
            # inspection.
            inner["concurrent_add"] = json.dumps(True)
            inner["tombstoned"] = json.dumps(False)
            return

        # Fresh entry.
        inner["value"] = json.dumps(value)
        inner["tombstoned"] = json.dumps(False)
        inner["concurrent_add"] = json.dumps(False)

    def remove(self, uuid: str) -> None:
        """Soft-delete *uuid* by setting its tombstone.

        If *uuid* does not exist in the map, this is a no-op (the item was
        never added on this peer — safe to ignore).
        """
        inner = self._entry(uuid)
        if inner is None:
            return
        inner["tombstoned"] = json.dumps(True)

    def get(self, uuid: str) -> dict | None:
        """Return the value for *uuid* if it is not tombstoned; else ``None``."""
        inner = self._entry(uuid)
        if inner is None:
            return None
        tombstoned_raw = inner.get("tombstoned")
        if tombstoned_raw is not None and json.loads(str(tombstoned_raw)):
            return None
        raw = inner.get("value")
        if raw is None:
            return None
        return json.loads(str(raw))  # type: ignore[return-value]

    def has(self, uuid: str) -> bool:
        """Return ``True`` when *uuid* exists and is not tombstoned."""
        return self.get(uuid) is not None

    def items(self) -> Iterator[tuple[str, dict]]:
        """Yield ``(uuid, value)`` for all non-tombstoned entries."""
        for key in self._ymap:
            value = self.get(key)
            if value is not None:
                yield key, value

    def has_concurrent_add_collision(self, uuid: str) -> bool:
        """Return ``True`` if a concurrent-add collision was detected for *uuid*."""
        inner = self._entry(uuid)
        if inner is None:
            return False
        raw = inner.get("concurrent_add")
        if raw is None:
            return False
        return bool(json.loads(str(raw)))
