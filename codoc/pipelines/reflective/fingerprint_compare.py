"""Compare current chunk fingerprints against stored fingerprints.

For each chunk visible in the current working tree, we compare against the
fingerprint stored in SQLite's ``chunk_fingerprints`` table.  We also check
every stored binding whose anchor lives in a changed or deleted file to catch
entities that were removed without any replacement chunk appearing.

Unchanged chunks (fingerprint matches stored value exactly) are silently
skipped so the pipeline scales with the size of the diff, not the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass

from codoc.lang import Chunk
from codoc.storage.sqlite_store import SQLiteStore
from codoc.core.fingerprint import fingerprint_chunk, are_fingerprints_meaningfully_different


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


def _chunk_cache_key(file: str, symbol_path: str) -> str:
    """Stable primary key for the ``chunk_fingerprints`` table."""
    return f"{file}::{symbol_path}"


def compare_chunk_fingerprints(
    chunks_by_file: dict[str, list[Chunk]],
    deleted_files: list[str],
    store: SQLiteStore,
    language_adapters: dict,
) -> list[ChunkChange]:
    """Identify every chunk that meaningfully changed relative to stored state.

    Parameters
    ----------
    chunks_by_file:
        Current-state chunks keyed by repo-relative file path.
        Only files that still exist AND have a supported language should appear
        here (i.e. the output of ``extract_chunks_for_files``).
    deleted_files:
        List of repo-relative file paths that were removed in this commit.
        All stored bindings anchored to these files are marked ``"removed"``.
    store:
        An open :class:`~codoc.storage.sqlite_store.SQLiteStore` instance.
    language_adapters:
        Mapping ``{language_name: adapter_instance}`` used to compute
        fingerprints for current chunks.  The adapter is chosen by matching
        ``chunk.file`` extension via the same logic as :func:`codoc.lang.detect_language`.

    Returns
    -------
    list[ChunkChange]
        One entry per chunk that is new, modified, or removed.
        Chunks whose fingerprint is identical to the stored value are omitted.
    """
    from codoc.lang import detect_language, get_adapter

    changes: list[ChunkChange] = []

    # Build an index of all existing bindings keyed by (file, symbol_path) for O(1) lookup.
    # We need the full binding list to detect orphaned anchors.
    all_bindings = store.get_all_bindings()
    binding_by_anchor: dict[tuple[str, str | None], str] = {}
    for binding in all_bindings:
        anchor = binding.anchor
        key = (anchor.file, anchor.symbol_path)
        binding_by_anchor[key] = binding.uuid

    # ------------------------------------------------------------------
    # Phase 1 — files that were deleted entirely in this commit
    # ------------------------------------------------------------------
    deleted_set: set[str] = set(deleted_files)
    for file in deleted_set:
        # Find any stored bindings anchored to this file.
        for binding in all_bindings:
            if binding.anchor.file != file:
                continue
            symbol_path = binding.anchor.symbol_path or ""
            stored_fp = store.get_chunk_fingerprint(_chunk_cache_key(file, symbol_path))
            changes.append(
                ChunkChange(
                    chunk=None,
                    symbol_path=symbol_path,
                    file=file,
                    change_kind="removed",
                    current_fingerprint=None,
                    stored_fingerprint=stored_fp,
                    existing_binding_uuid=binding.uuid,
                )
            )

        # Also emit removals for fingerprint-cache entries (unattributed chunks)
        # that lived in this file, so the cache stays consistent.
        all_fp_rows = store.get_all_chunk_fingerprints()
        for cache_key, stored_fp in all_fp_rows.items():
            if not cache_key.startswith(file + "::"):
                continue
            symbol_path = cache_key[len(file) + 2:]
            # Skip if we already emitted this via a binding.
            if binding_by_anchor.get((file, symbol_path)):
                continue
            changes.append(
                ChunkChange(
                    chunk=None,
                    symbol_path=symbol_path,
                    file=file,
                    change_kind="removed",
                    current_fingerprint=None,
                    stored_fingerprint=stored_fp,
                    existing_binding_uuid=None,
                )
            )

    # ------------------------------------------------------------------
    # Phase 2 — files that still exist: compare chunk-by-chunk
    # ------------------------------------------------------------------
    seen_cache_keys: set[str] = set()

    for file, chunks in chunks_by_file.items():
        # Pick the adapter once per file.
        language = detect_language(file)
        if language is None:
            continue
        adapter = language_adapters.get(language)
        if adapter is None:
            try:
                adapter = get_adapter(language)
            except ValueError:
                continue

        for chunk in chunks:
            symbol_path = chunk.symbol_path
            cache_key = _chunk_cache_key(file, symbol_path)
            seen_cache_keys.add(cache_key)

            current_fp = fingerprint_chunk(chunk.source, adapter)
            stored_fp = store.get_chunk_fingerprint(cache_key)

            # Determine existing binding (if any).
            existing_uuid = binding_by_anchor.get((file, symbol_path))

            if stored_fp is None:
                # Brand-new chunk never seen before.
                changes.append(
                    ChunkChange(
                        chunk=chunk,
                        symbol_path=symbol_path,
                        file=file,
                        change_kind="added",
                        current_fingerprint=current_fp,
                        stored_fingerprint=None,
                        existing_binding_uuid=existing_uuid,
                    )
                )
            elif are_fingerprints_meaningfully_different(stored_fp, current_fp):
                # Known chunk whose content changed.
                changes.append(
                    ChunkChange(
                        chunk=chunk,
                        symbol_path=symbol_path,
                        file=file,
                        change_kind="modified",
                        current_fingerprint=current_fp,
                        stored_fingerprint=stored_fp,
                        existing_binding_uuid=existing_uuid,
                    )
                )
            # else: fingerprint unchanged → skip (no-op).

    # ------------------------------------------------------------------
    # Phase 3 — stored bindings whose anchor files were not deleted but
    # whose symbol_path no longer appears among current chunks.
    # These indicate that an entity was removed or renamed within a file
    # that otherwise still exists.
    # ------------------------------------------------------------------
    current_file_set = set(chunks_by_file.keys())
    for binding in all_bindings:
        file = binding.anchor.file
        symbol_path = binding.anchor.symbol_path or ""
        if file in deleted_set:
            # Already handled in Phase 1.
            continue
        if file not in current_file_set:
            # File wasn't touched in this commit at all — not our concern.
            continue

        cache_key = _chunk_cache_key(file, symbol_path)
        if cache_key in seen_cache_keys:
            # We already processed this chunk in Phase 2.
            continue

        # The binding's anchor points to a chunk that no longer exists in
        # the re-parsed file → treat as removed.
        stored_fp = store.get_chunk_fingerprint(cache_key)
        changes.append(
            ChunkChange(
                chunk=None,
                symbol_path=symbol_path,
                file=file,
                change_kind="removed",
                current_fingerprint=None,
                stored_fingerprint=stored_fp,
                existing_binding_uuid=binding.uuid,
            )
        )

    return changes
