"""codoc.pipelines.intentional.runner — convenience dispatcher.

Provides ``open_stores`` (a factory function) and ``IntentionalRunner`` (a
context-manager wrapper) so callers do not have to wire up SQLiteStore,
JSONLLog, and TransactionLog manually.
"""

from __future__ import annotations

import os
from pathlib import Path

from codoc.storage.sqlite_store import SQLiteStore
from codoc.storage.jsonl_log import JSONLLog
from codoc.core.log import TransactionLog
from codoc.model.feature import Feature
from codoc.model.obligation import Obligation
from codoc.model.transaction import Transaction
from codoc.pipelines.intentional.amend import amend_feature
from codoc.pipelines.intentional.merge import merge_features
from codoc.pipelines.intentional.rename import rename_feature
from codoc.pipelines.intentional.restructure import restructure_feature
from codoc.pipelines.intentional.retire import retire_feature
from codoc.pipelines.intentional.rewind import rewind_feature
from codoc.pipelines.intentional.split import split_feature


# ---------------------------------------------------------------------------
# Shared helper used by all handlers
# ---------------------------------------------------------------------------


def _get_feature_or_raise(feature_uuid: str, store: SQLiteStore) -> Feature:
    """Get feature from store or raise ValueError with a clear message."""
    feature = store.get_feature(feature_uuid)
    if feature is None:
        raise ValueError(f"Feature {feature_uuid!r} not found")
    return feature


# ---------------------------------------------------------------------------
# Store factory
# ---------------------------------------------------------------------------


def open_stores(
    codoc_dir: str,
    node_id: str = "default",
) -> tuple[SQLiteStore, JSONLLog, TransactionLog]:
    """Open SQLiteStore, JSONLLog, and TransactionLog from the .codoc directory.

    Expects *codoc_dir* to be the path of the ``.codoc`` directory (already
    existing or creatable).  Creates the directory if it does not yet exist.

    Returns ``(store, jsonl_log, tx_log)``.  The caller is responsible for
    closing *store* when done (``store.close()`` or ``with store: ...``).
    """
    base = Path(codoc_dir)
    base.mkdir(parents=True, exist_ok=True)

    db_path = str(base / "codoc.db")
    log_path = str(base / "log.jsonl")

    store = SQLiteStore(db_path)
    store.open()

    jsonl_log = JSONLLog(log_path)
    tx_log = TransactionLog(store, node_id=node_id)

    return store, jsonl_log, tx_log


# ---------------------------------------------------------------------------
# Context-manager wrapper
# ---------------------------------------------------------------------------


class IntentionalRunner:
    """Convenience wrapper that opens and holds stores for the codoc_dir.

    Usage::

        with IntentionalRunner("/path/to/.codoc", author="alice") as runner:
            tx = runner.amend(feature_uuid, "New intent prose.")
            tx = runner.rename(feature_uuid, "new-slug")
            tx = runner.retire(feature_uuid)

    The underlying SQLiteStore is closed automatically on ``__exit__``.
    """

    def __init__(
        self,
        codoc_dir: str,
        author: str = "user",
        node_id: str = "default",
    ) -> None:
        self._codoc_dir = codoc_dir
        self._author = author
        self._node_id = node_id
        self._store: SQLiteStore | None = None
        self._jsonl_log: JSONLLog | None = None
        self._tx_log: TransactionLog | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "IntentionalRunner":
        self._store, self._jsonl_log, self._tx_log = open_stores(
            self._codoc_dir, node_id=self._node_id
        )
        return self

    def __exit__(self, *args: object) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None
        self._jsonl_log = None
        self._tx_log = None

    # ------------------------------------------------------------------
    # Internal guard
    # ------------------------------------------------------------------

    @property
    def _open_store(self) -> SQLiteStore:
        if self._store is None:
            raise RuntimeError(
                "IntentionalRunner is not open. Use it as a context manager."
            )
        return self._store

    @property
    def _open_jsonl(self) -> JSONLLog:
        if self._jsonl_log is None:
            raise RuntimeError(
                "IntentionalRunner is not open. Use it as a context manager."
            )
        return self._jsonl_log

    @property
    def _open_tx_log(self) -> TransactionLog:
        if self._tx_log is None:
            raise RuntimeError(
                "IntentionalRunner is not open. Use it as a context manager."
            )
        return self._tx_log

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    def amend(self, feature_uuid: str, new_intent: str) -> Transaction:
        """Edit a feature's intent prose and return the committed Transaction."""
        return amend_feature(
            feature_uuid=feature_uuid,
            new_intent=new_intent,
            store=self._open_store,
            tx_log=self._open_tx_log,
            jsonl_log=self._open_jsonl,
            author=self._author,
        )

    def rename(self, feature_uuid: str, new_slug: str) -> Transaction:
        """Edit a feature's slug and return the committed Transaction."""
        return rename_feature(
            feature_uuid=feature_uuid,
            new_slug=new_slug,
            store=self._open_store,
            tx_log=self._open_tx_log,
            jsonl_log=self._open_jsonl,
            author=self._author,
        )

    def retire(self, feature_uuid: str) -> Transaction:
        """Mark a feature as retired and return the committed Transaction."""
        return retire_feature(
            feature_uuid=feature_uuid,
            store=self._open_store,
            tx_log=self._open_tx_log,
            jsonl_log=self._open_jsonl,
            author=self._author,
        )

    def split(
        self,
        feature_uuid: str,
        child_a_slug: str,
        child_a_intent: str,
        child_a_binding_uuids: list[str],
        child_b_slug: str,
        child_b_intent: str,
        child_b_binding_uuids: list[str],
        binding_graph: dict[str, set[str]] | None = None,
    ) -> tuple[Transaction, list[Obligation]]:
        """Split a feature into two children; returns (tx, obligations)."""
        return split_feature(
            feature_uuid=feature_uuid,
            child_a_slug=child_a_slug,
            child_a_intent=child_a_intent,
            child_a_binding_uuids=child_a_binding_uuids,
            child_b_slug=child_b_slug,
            child_b_intent=child_b_intent,
            child_b_binding_uuids=child_b_binding_uuids,
            store=self._open_store,
            tx_log=self._open_tx_log,
            jsonl_log=self._open_jsonl,
            author=self._author,
            binding_graph=binding_graph,
        )

    def merge(
        self,
        source_uuids: list[str],
        target_slug: str,
        target_intent: str,
        binding_graph: dict[str, set[str]] | None = None,
    ) -> tuple[Transaction, list[Obligation]]:
        """Merge multiple features into one new target; returns (tx, obligations)."""
        return merge_features(
            source_uuids=source_uuids,
            target_slug=target_slug,
            target_intent=target_intent,
            store=self._open_store,
            tx_log=self._open_tx_log,
            jsonl_log=self._open_jsonl,
            author=self._author,
            binding_graph=binding_graph,
        )

    def restructure(
        self,
        feature_uuid: str,
        new_parent_uuid: str | None,
        binding_graph: dict[str, set[str]] | None = None,
    ) -> tuple[Transaction, list[Obligation]]:
        """Move a feature to a new parent; returns (tx, obligations)."""
        return restructure_feature(
            feature_uuid=feature_uuid,
            new_parent_uuid=new_parent_uuid,
            store=self._open_store,
            tx_log=self._open_tx_log,
            jsonl_log=self._open_jsonl,
            author=self._author,
            binding_graph=binding_graph,
        )

    def rewind(
        self,
        feature_uuid: str,
        target_hlc_str: str,
        binding_graph: dict[str, set[str]] | None = None,
    ) -> tuple[Transaction, list[Obligation]]:
        """Rewind a feature's intent/slug to a prior HLC state; returns (tx, obligations)."""
        return rewind_feature(
            feature_uuid=feature_uuid,
            target_hlc_str=target_hlc_str,
            store=self._open_store,
            tx_log=self._open_tx_log,
            jsonl_log=self._open_jsonl,
            author=self._author,
            binding_graph=binding_graph,
        )
