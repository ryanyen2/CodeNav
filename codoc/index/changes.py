"""What changed in the code: a diff of the chunk index before and after an update.

``compute_changeset`` snapshots the ``code_chunks`` table, runs an incremental
``update_index``, snapshots again, keys both by ``symbol`` and compares
``fingerprint`` to classify every chunk as added, removed or modified.
Deterministic; relocations are recognised later, by the reflect pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from codoc.index.reader import ChunkRow, read_all_chunks
from codoc.index.runner import update_index


@dataclass
class ChunkRef:
    """A chunk involved in a change. ``source`` is the new source for an added
    or modified chunk, the old one for a removed chunk.

    ``fingerprint`` is the chunk's content, wherever it sits; ``types_hash`` is
    its shape, whatever its names. Together they let the reflect pass
    recognise a removed and an added chunk as one that moved or was renamed."""

    symbol: str
    fingerprint: str = ""
    types_hash: str = ""
    source: str = ""

    @property
    def file(self) -> str:
        return self.symbol.split("::", 1)[0]


@dataclass
class ChangeSet:
    added: list[ChunkRef] = field(default_factory=list)
    removed: list[ChunkRef] = field(default_factory=list)
    modified: list[ChunkRef] = field(default_factory=list)
    rows: list[ChunkRow] = field(default_factory=list)  # post-update index snapshot

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

    old_by_symbol = {r.symbol: r for r in old_rows}
    new_by_symbol = {r.symbol: r for r in new_rows}

    cs = ChangeSet(rows=all_new_rows)
    for symbol in old_by_symbol.keys() | new_by_symbol.keys():
        old = old_by_symbol.get(symbol)
        new = new_by_symbol.get(symbol)
        if old is None:
            cs.added.append(ChunkRef(symbol, new.fingerprint, new.types_hash, new.source))
        elif new is None:
            cs.removed.append(ChunkRef(symbol, old.fingerprint, old.types_hash, old.source))
        elif old.fingerprint != new.fingerprint:
            cs.modified.append(ChunkRef(symbol, new.fingerprint, new.types_hash, new.source))
    return cs
