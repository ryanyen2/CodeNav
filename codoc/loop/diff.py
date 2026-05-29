"""Index snapshot diff — the deterministic, embedding-free change detector.

Snapshots the cocoindex/LanceDB ``code_chunks`` table before and after an
incremental ``update_index``, keys both by ``(file, symbol_path)``, and compares
``tokens_hash`` to classify every chunk as added / removed / modified. This is
the whole of "what changed in the code" — no move/fracture/coalesce machinery.

Lifted from the old ``reflective.runner._build_diff`` (the classification half),
minus the binding-anchor synthesis and the move-detection scaffolding.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from codoc.pipelines.indexing.reader import ChunkRow, read_all_chunks
from codoc.pipelines.indexing.runner import update_index


@dataclass
class ChunkRef:
    """A chunk involved in a change. ``source`` is the new source for
    added/modified, or the pre-change source for removed (best-effort).

    ``fingerprint`` is the chunk's ``tokens_hash`` (content identity, move-
    invariant); ``types_hash`` is its AST-shape identity (rename-invariant).
    Together they let Loop A recognise a remove+add pair as a relocation."""

    file: str
    symbol_path: str
    fingerprint: str = ""
    source: str = ""
    types_hash: str = ""


@dataclass
class ChangeSet:
    added: list[ChunkRef] = field(default_factory=list)
    removed: list[ChunkRef] = field(default_factory=list)
    modified: list[ChunkRef] = field(default_factory=list)
    rows: list[ChunkRow] = field(default_factory=list)  # post-update index snapshot

    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.modified)

    def touched_files(self) -> set[str]:
        return {c.file for c in (self.added + self.removed + self.modified)}

    def fingerprints(self) -> dict[tuple[str, str], str]:
        """(file, symbol_path) → fingerprint for every added/modified chunk."""
        out: dict[tuple[str, str], str] = {}
        for c in self.added + self.modified:
            out[(c.file, c.symbol_path)] = c.fingerprint
        return out

    def types_hashes(self) -> dict[tuple[str, str], str]:
        """(file, symbol_path) → types_hash for every added/modified chunk.

        Recorded onto the binding at attribution time so the state-based
        reconciler can still recognise a later RENAME (same AST shape, new name)
        after the old symbol has left the index."""
        out: dict[tuple[str, str], str] = {}
        for c in self.added + self.modified:
            if c.types_hash:
                out[(c.file, c.symbol_path)] = c.types_hash
        return out


def _scope(rows: list[ChunkRow], file_scope: set[str] | None) -> list[ChunkRow]:
    if file_scope is None:
        return rows
    return [r for r in rows if r.file in file_scope]


def compute_changeset(
    root_dir: str,
    codoc_dir: str,
    *,
    file_scope: set[str] | None = None,
) -> ChangeSet:
    """Diff the index before/after an incremental update; return the change set.

    ``file_scope`` restricts comparison to a set of repo-relative files (a watch
    cycle reports exactly which files changed) — the index update is still global
    but cheap thanks to cocoindex per-file memoization.
    """
    old_rows = _scope(read_all_chunks(codoc_dir), file_scope)
    update_index(root_dir, codoc_dir)
    all_new_rows = read_all_chunks(codoc_dir)
    new_rows = _scope(all_new_rows, file_scope)

    old_by_key = {(r.file, r.symbol_path): r for r in old_rows}
    new_by_key = {(r.file, r.symbol_path): r for r in new_rows}

    cs = ChangeSet(rows=all_new_rows)
    for key in set(old_by_key) | set(new_by_key):
        file, symbol_path = key
        old = old_by_key.get(key)
        new = new_by_key.get(key)
        if old is None and new is not None:
            cs.added.append(ChunkRef(file, symbol_path, new.tokens_hash, new.source, new.types_hash))
        elif old is not None and new is None:
            cs.removed.append(ChunkRef(file, symbol_path, old.tokens_hash, old.source, old.types_hash))
        elif old is not None and new is not None and old.tokens_hash != new.tokens_hash:
            cs.modified.append(ChunkRef(file, symbol_path, new.tokens_hash, new.source, new.types_hash))
    return cs
