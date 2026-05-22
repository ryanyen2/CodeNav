"""Shared types for the reflective pipeline.

``ChunkChange`` is the internal representation of a chunk that the reflective
pipeline must reason about (added, modified, or removed relative to stored state).
"""

from __future__ import annotations

from dataclasses import dataclass

from codoc.lang import Chunk


@dataclass
class ChunkChange:
    """Describes a single chunk that the reflective pipeline must reason about."""

    chunk: Chunk | None
    """The current chunk object.  ``None`` when the chunk was deleted."""

    symbol_path: str
    """Always set, even when *chunk* is ``None`` (derived from stored anchor)."""

    file: str
    """Repo-relative posix path of the file that contains / contained the chunk."""

    change_kind: str
    """One of ``"added"`` | ``"modified"`` | ``"removed"``."""

    current_fingerprint: str | None
    """SHA-256 fingerprint of the chunk in the current working tree.
    ``None`` when *change_kind* is ``"removed"``."""

    stored_fingerprint: str | None
    """SHA-256 fingerprint previously stored in SQLite.
    ``None`` when *change_kind* is ``"added"`` (never seen before)."""

    existing_binding_uuid: str | None
    """UUID of the Binding in the feature map whose anchor points to this
    symbol path, if one exists.  ``None`` for unattributed chunks."""


def chunk_cache_key(file: str, symbol_path: str) -> str:
    """Stable primary key for the ``chunk_fingerprints`` table."""
    return f"{file}::{symbol_path}"
