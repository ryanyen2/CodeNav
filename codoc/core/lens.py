"""Lens facade over the projection layer.

The lens is the bidirectional transformation between the SQLite feature graph
and the human-editable ``.codoc/tree/`` document format.

    get(codoc_dir, store, tx_log)  →  renders Store → Doc (writes _index.codoc + sidecar).
    put(codoc_dir, store, tx_log)  →  reads Doc edits → Store (applies IntentOps).

The six projection modules (``tree_codoc``, ``parser``, ``sync``, ``differ``,
``tree_align``, ``meta``) are the implementation of this lens.  This module
gives them a single named entry point.

Callers
-------
    from codoc.core.lens import get, put

    # Render the current store state to .codoc/tree/:
    get(codoc_dir, store, tx_log)

    # Sync user edits from .codoc/tree/ back to the store:
    result = put(codoc_dir, store, tx_log)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codoc.core.log import TransactionLog
    from codoc.projection.sync import SyncResult
    from codoc.storage.sqlite_store import SQLiteStore


def get(codoc_dir: str, store: "SQLiteStore", tx_log: "TransactionLog") -> None:
    """Render the feature graph to ``.codoc/tree/`` (Store → Doc).

    Delegates to ``codoc.projection.tree_codoc.write_tree``.
    """
    from codoc.projection.tree_codoc import write_tree
    write_tree(codoc_dir, store, tx_log)


def put(codoc_dir: str, store: "SQLiteStore", tx_log: "TransactionLog") -> "SyncResult":
    """Apply user edits from ``.codoc/tree/`` back to the store (Doc → Store).

    Delegates to ``codoc.projection.sync.sync_from_dir``.
    """
    from codoc.projection.sync import sync_from_dir
    return sync_from_dir(codoc_dir, store, tx_log)
