"""What changed in the code: a diff of the chunk index before and after an update.

``compute_changeset`` snapshots the ``code_chunks`` table, runs an incremental
``update_index``, snapshots again, keys both by ``(file, symbol_path)`` and
compares ``tokens_hash`` to classify every chunk as added, removed or modified.
Deterministic; relocations are recognised later, by the reflect pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from codoc.index.reader import ChunkRow, read_all_chunks
from codoc.index.runner import update_index


@dataclass
class ChunkRef:
    """A chunk involved in a change. ``source`` is the new source for
    added/modified, or the pre-change source for removed (best-effort).

    ``fingerprint`` is the chunk's ``tokens_hash`` (content identity, move-
    invariant); ``types_hash`` is its AST-shape identity (rename-invariant).
    Together they let the reflect pass recognise a remove+add pair as a relocation."""

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


def compute_changeset(
    root_dir: str,
    codoc_dir: str,
    *,
    file_scope: set[str] | None = None,
) -> ChangeSet:
    """Diff the index before/after an incremental update; return the change set.

    ``file_scope`` restricts comparison to a set of repo-relative files (a watch
    cycle reports exactly which files changed). The index update is still global
    but cheap thanks to cocoindex per-file memoization. The scope is pushed down
    to LanceDB (``files=``) so a scoped pass reads only the touched files' rows.
    ``rows`` (the graph's symbol table) still spans the whole index, but
    without source: the resolver only needs symbol identity, and the graph
    only re-extracts edges from the touched files, whose sourced rows are
    merged in below.
    """
    old_rows = read_all_chunks(codoc_dir, files=file_scope)
    update_index(root_dir, codoc_dir)
    if file_scope is None:
        all_new_rows = read_all_chunks(codoc_dir)
        new_rows = all_new_rows
    else:
        new_rows = read_all_chunks(codoc_dir, files=file_scope)
        light = read_all_chunks(codoc_dir, with_source=False)
        all_new_rows = [r for r in light if r.file not in file_scope] + new_rows

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
