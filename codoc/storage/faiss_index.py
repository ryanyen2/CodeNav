"""FAISS index for chunk embeddings.

Provides incremental add/remove with a tombstone + rebuild-on-threshold pattern.
The index is persisted to disk as two files inside ``index_dir/``:

- ``faiss.index`` — the raw FAISS binary index.
- ``faiss_meta.pkl`` — pickled ``(entries, dim)`` where *entries* is the
  parallel list of ``{key, metadata, tombstoned}`` dicts.

Tombstones let us mark an entry as removed without immediately rebuilding the
index. When the tombstone ratio exceeds :attr:`REBUILD_TOMBSTONE_RATIO`, a
hard rebuild is triggered automatically, keeping memory and search cost low.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import faiss


class FaissIndex:
    """L2-distance flat FAISS index with incremental update support.

    All vectors must have the same dimension. The dimension is inferred from
    the first :meth:`add` call and is fixed thereafter.

    Usage::

        idx = FaissIndex(".codoc/faiss/")
        idx.open()
        idx.add("file.py::MyClass", embedding_vector, {"file": "file.py"})
        results = idx.search(query_vector, k=5)
        idx.save()
    """

    REBUILD_TOMBSTONE_RATIO: float = 0.3  # rebuild when >= 30 % of entries are tombstoned

    _INDEX_FILE = "faiss.index"
    _META_FILE = "faiss_meta.pkl"

    def __init__(self, index_dir: str) -> None:
        self._dir = Path(index_dir)
        self._index: faiss.IndexFlatL2 | None = None
        # Each element mirrors a FAISS row: {key: str, metadata: dict, tombstoned: bool}
        self._entries: list[dict] = []
        self._dim: int | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Load from disk if the index files exist; otherwise create an empty state.

        The FAISS index dimension is deferred until the first :meth:`add` call
        when starting from scratch.
        """
        index_path = self._dir / self._INDEX_FILE
        meta_path = self._dir / self._META_FILE

        if index_path.exists() and meta_path.exists():
            self._index = faiss.read_index(str(index_path))
            with meta_path.open("rb") as fh:
                self._entries, self._dim = pickle.load(fh)
        else:
            # Empty state; index will be created on first add().
            self._index = None
            self._entries = []
            self._dim = None

    def save(self) -> None:
        """Persist the FAISS index and metadata to disk.

        Creates ``index_dir/`` if it does not exist. If no vectors have been
        added yet, nothing is written.
        """
        if self._index is None:
            # Nothing to persist yet.
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(self._dir / self._INDEX_FILE))
        with (self._dir / self._META_FILE).open("wb") as fh:
            pickle.dump((self._entries, self._dim), fh)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, key: str, vector: list[float], metadata: dict) -> None:
        """Add or update an entry.

        If *key* already exists (non-tombstoned), the old row is tombstoned
        first, then the new vector is appended. This avoids an expensive
        rebuild on every update while keeping result quality high.

        Args:
            key: Stable identifier for this entry (e.g. ``"path/file.py::ClassName"``).
            vector: Embedding vector. All vectors must share the same dimension.
            metadata: Arbitrary dict stored alongside the entry and returned by :meth:`search`.
        """
        vec = np.array(vector, dtype=np.float32).reshape(1, -1)
        dim = vec.shape[1]

        # Initialise the FAISS index on first add.
        if self._index is None:
            self._dim = dim
            self._index = faiss.IndexFlatL2(dim)
        elif dim != self._dim:
            raise ValueError(
                f"Vector dimension mismatch: index expects {self._dim}, got {dim}."
            )

        # Tombstone any existing entry with the same key.
        for entry in self._entries:
            if entry["key"] == key and not entry["tombstoned"]:
                entry["tombstoned"] = True

        self._index.add(vec)
        self._entries.append({"key": key, "metadata": metadata, "tombstoned": False})

        self._rebuild_if_needed()

    def remove(self, key: str) -> None:
        """Tombstone all non-tombstoned entries matching *key*.

        FAISS ``IndexFlatL2`` does not support in-place removal, so we use
        tombstones. A rebuild is triggered if the tombstone ratio crosses the
        threshold.
        """
        found = False
        for entry in self._entries:
            if entry["key"] == key and not entry["tombstoned"]:
                entry["tombstoned"] = True
                found = True
        if found:
            self._rebuild_if_needed()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query_vector: list[float], k: int = 10) -> list[dict]:
        """Return the top-*k* non-tombstoned entries by L2 distance.

        Args:
            query_vector: Query embedding vector (must match index dimension).
            k: Maximum number of results to return.

        Returns:
            List of ``metadata`` dicts for the closest entries, ordered by
            ascending L2 distance. May return fewer than *k* results if fewer
            non-tombstoned entries exist.
        """
        if self._index is None or self._index.ntotal == 0:
            return []

        active_count = sum(1 for e in self._entries if not e["tombstoned"])
        if active_count == 0:
            return []

        # We may need to over-fetch to account for tombstoned rows.
        search_k = min(self._index.ntotal, max(k, k + len(self._entries) - active_count))
        query = np.array(query_vector, dtype=np.float32).reshape(1, -1)
        _distances, indices = self._index.search(query, search_k)

        results: list[dict] = []
        for idx in indices[0]:
            if idx < 0 or idx >= len(self._entries):
                continue
            entry = self._entries[idx]
            if entry["tombstoned"]:
                continue
            results.append(entry["metadata"])
            if len(results) >= k:
                break

        return results

    # ------------------------------------------------------------------
    # Rebuild helpers
    # ------------------------------------------------------------------

    def _rebuild_if_needed(self) -> None:
        """Trigger a hard rebuild when the tombstone ratio exceeds the threshold."""
        total = len(self._entries)
        if total == 0:
            return
        tombstoned = sum(1 for e in self._entries if e["tombstoned"])
        if tombstoned / total >= self.REBUILD_TOMBSTONE_RATIO:
            self._rebuild()

    def _rebuild(self) -> None:
        """Hard-rebuild: discard tombstoned rows and create a fresh FAISS index.

        This is a O(n) operation over the live entries. It requires re-adding
        all non-tombstoned vectors, which means we must fetch them from the
        current index using ``reconstruct()``.

        Note: ``IndexFlatL2`` supports ``reconstruct()``; other index types
        may not. If a different index type is needed in the future, store
        vectors in ``_entries`` alongside metadata.
        """
        if self._index is None or self._dim is None:
            return

        live_entries = [e for e in self._entries if not e["tombstoned"]]
        if not live_entries:
            self._index = faiss.IndexFlatL2(self._dim)
            self._entries = []
            return

        # Reconstruct vectors for live entries by their original FAISS row indices.
        live_indices = [i for i, e in enumerate(self._entries) if not e["tombstoned"]]
        vectors = np.vstack([
            self._index.reconstruct(int(i)).reshape(1, -1) for i in live_indices
        ])

        new_index = faiss.IndexFlatL2(self._dim)
        new_index.add(vectors)

        self._index = new_index
        self._entries = [
            {"key": e["key"], "metadata": e["metadata"], "tombstoned": False}
            for e in live_entries
        ]

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def all_keys(self) -> list[str]:
        """Return the keys of all non-tombstoned entries."""
        return [e["key"] for e in self._entries if not e["tombstoned"]]
