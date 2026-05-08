"""
codoc.core.crdt.or_set — Observed-Remove set backed by a pycrdt Y.Map.

Semantics
---------
Each *item* has a stable UUID (the "item_uuid") and one or more UUIDv7
*add-tags*.  The backing Y.Map stores one entry per item_uuid:

    ymap[item_uuid] = {
        "value":       <JSON-serialised dict>,
        "add_tags":    <JSON-serialised list[str]>,   # UUIDv7 per-add
        "remove_tags": <JSON-serialised list[str]>,   # observed add-tags at remove time
    }

An item is **active** when ``set(add_tags) - set(remove_tags)`` is non-empty.

Concurrent add + remove on the same item_uuid:
    - add mints a fresh UUIDv7 add-tag that the concurrent remove has not seen.
    - The remove moves only the *currently observed* add-tags into remove_tags.
    - After merge, the new add-tag is not cancelled  →  add wins.

UUIDv7 generation uses ``uuid6.uuid7()`` when available, falling back to
``uuid.uuid4()`` (random UUIDs retain the OR-set correctness guarantee; only
the time-ordering property is lost).
"""

from __future__ import annotations

import json

import pycrdt

# ---------------------------------------------------------------------------
# UUIDv7 helper — graceful fallback to uuid4 if uuid6 not installed.
# ---------------------------------------------------------------------------

def _new_add_tag() -> str:
    try:
        import uuid6  # type: ignore[import-untyped]
        return str(uuid6.uuid7())
    except ImportError:
        import uuid
        return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# ORSet
# ---------------------------------------------------------------------------

class ORSet:
    """Observed-Remove set backed by a pycrdt Y.Map.

    Parameters
    ----------
    ymap:
        The Y.Map that acts as the backing store.  Each ``item_uuid`` key
        holds a nested Y.Map with ``value``, ``add_tags``, and
        ``remove_tags`` fields (all JSON-serialised).
    """

    def __init__(self, ymap: pycrdt.Map) -> None:
        self._ymap = ymap

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _entry(self, item_uuid: str) -> pycrdt.Map | None:
        entry = self._ymap.get(item_uuid)
        if entry is None:
            return None
        return entry  # type: ignore[return-value]

    def _get_or_create_entry(self, item_uuid: str, value: dict) -> pycrdt.Map:
        entry = self._ymap.get(item_uuid)
        if entry is None:
            # Insert the empty Y.Map first so it is integrated into the doc,
            # then populate its fields (writing to an unintegrated Y.Map raises).
            inner: pycrdt.Map = pycrdt.Map()
            self._ymap[item_uuid] = inner
            integrated: pycrdt.Map = self._ymap[item_uuid]  # type: ignore[assignment]
            integrated["value"] = json.dumps(value)
            integrated["add_tags"] = json.dumps([])
            integrated["remove_tags"] = json.dumps([])
        return self._ymap[item_uuid]  # type: ignore[return-value]

    def _add_tags(self, inner: pycrdt.Map) -> list[str]:
        raw = inner.get("add_tags")
        if raw is None:
            return []
        return json.loads(str(raw))

    def _remove_tags(self, inner: pycrdt.Map) -> list[str]:
        raw = inner.get("remove_tags")
        if raw is None:
            return []
        return json.loads(str(raw))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, item_uuid: str, value: dict) -> str:
        """Add *item_uuid* to the set with *value*.

        Generates a fresh UUIDv7 add-tag and appends it to ``add_tags``.
        The item is considered active immediately (add-tag not in remove_tags).

        Returns the add-tag UUID so callers can track individual additions
        if needed.
        """
        inner = self._get_or_create_entry(item_uuid, value)

        # Always update value to the latest provided.
        inner["value"] = json.dumps(value)

        add_tag = _new_add_tag()
        current_add_tags = self._add_tags(inner)
        current_add_tags.append(add_tag)
        inner["add_tags"] = json.dumps(current_add_tags)

        return add_tag

    def remove(self, item_uuid: str) -> None:
        """Remove *item_uuid* from the set (Observed-Remove).

        Moves all *currently visible* add-tags into ``remove_tags``.
        A concurrent add that mints a new add-tag after this call (but before
        the remove is observed by that peer) will not be cancelled — add wins.

        If *item_uuid* is not in the map, this is a no-op.
        """
        inner = self._entry(item_uuid)
        if inner is None:
            return

        current_add_tags = self._add_tags(inner)
        current_remove_tags = self._remove_tags(inner)

        # Union existing remove_tags with all currently visible add_tags.
        merged_remove = list(set(current_remove_tags) | set(current_add_tags))
        inner["remove_tags"] = json.dumps(merged_remove)

    def is_active(self, item_uuid: str) -> bool:
        """Return ``True`` when *item_uuid* is in the set (has uncancelled add-tags)."""
        inner = self._entry(item_uuid)
        if inner is None:
            return False
        active = set(self._add_tags(inner)) - set(self._remove_tags(inner))
        return len(active) > 0

    def active_items(self) -> list[dict]:
        """Return value dicts for all active items (those with uncancelled add-tags)."""
        result: list[dict] = []
        for key in self._ymap:
            if self.is_active(key):
                inner = self._entry(key)
                if inner is not None:
                    raw = inner.get("value")
                    if raw is not None:
                        result.append(json.loads(str(raw)))
        return result

    def active_item_uuids(self) -> list[str]:
        """Return item_uuids for all active items."""
        return [key for key in self._ymap if self.is_active(key)]
