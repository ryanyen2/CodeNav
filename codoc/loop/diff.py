"""Index snapshot diff — the deterministic, embedding-free change detector.

Snapshots the cocoindex/LanceDB ``code_chunks`` table before and after an
incremental ``update_index``, keys both by ``(file, symbol_path)``, and compares
``tokens_hash`` to classify every chunk as added / removed / modified. This is
the whole of "what changed in the code" — no move/fracture/coalesce machinery.

Lifted from the old ``reflective.runner._build_diff`` (the classification half),
minus the binding-anchor synthesis and the move-detection scaffolding.
"""
from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass, field

from codoc.lang import parses_cleanly
from codoc.pipelines.indexing.reader import ChunkRow, read_all_chunks
from codoc.pipelines.indexing.runner import update_index

_log = logging.getLogger(__name__)


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


def compute_changeset(
    root_dir: str,
    codoc_dir: str,
    *,
    file_scope: set[str] | None = None,
) -> ChangeSet:
    """Diff the index before/after an incremental update; return the change set.

    ``file_scope`` restricts comparison to a set of repo-relative files (a watch
    cycle reports exactly which files changed) — the index update is still global
    but cheap thanks to cocoindex per-file memoization. The scope is pushed down
    to LanceDB (``files=``) so a scoped pass reads only the touched files' rows;
    embeddings are never read here (the loops don't use them). ``rows`` (the
    graph's symbol table) still spans the whole index, but as a source-less,
    embedding-less projection — the resolver only needs symbol identity, and
    ``update_graph`` only re-extracts edges from the touched files, whose
    sourced rows are merged in below.
    """
    old_rows = read_all_chunks(codoc_dir, files=file_scope, with_embeddings=False)
    update_index(root_dir, codoc_dir)
    if file_scope is None:
        all_new_rows = read_all_chunks(codoc_dir, with_embeddings=False)
        new_rows = all_new_rows
    else:
        new_rows = read_all_chunks(codoc_dir, files=file_scope, with_embeddings=False)
        light = read_all_chunks(codoc_dir, with_embeddings=False, with_source=False)
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
    _hold_unparseable_removals(cs, root_dir)
    return cs


def _hold_unparseable_removals(cs: ChangeSet, root_dir: str) -> list[ChunkRef]:
    """Drop removals whose file is still on disk but no longer parses; return them.

    A chunk's absence from the index means one of two very different things, and
    the diff cannot tell them apart on its own: the entity was deleted, or the
    file was saved mid-edit and the parser could not reach it. Loop A reads a
    removal as deletion — a bound removal DETACHES the binding — so a broken save
    would strip a feature's attribution, and the repaired file would come back as
    an unbound ADDITION whose feature the LLM pass has to guess at again. The
    changeset for that save reports one thing honestly: nothing is known about
    that file.

    Only removals are held. An addition or a modification from a damaged file is
    at worst a spurious refresh — idempotent, and the next clean pass corrects it
    — while a removal destroys attribution that nothing recreates.

    A file that is GONE from disk removes its chunks for real, and one that no
    adapter can read (so ``parses_cleanly`` cannot judge it) is not second-guessed
    either: this holds a removal only where the file is present and demonstrably
    unparseable. A file that never parses — a templated ``.py``, Python 2 — keeps
    its stale bindings until it does, which is why the hold is logged rather than
    silent.
    """
    if not cs.removed:
        return []
    root = pathlib.Path(root_dir)
    broken: set[str] = set()
    for file in {c.file for c in cs.removed}:
        path = root / file
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # gone, unreadable → the removal stands
        if not parses_cleanly(file, source):
            broken.add(file)
    if not broken:
        return []
    held = [c for c in cs.removed if c.file in broken]
    cs.removed = [c for c in cs.removed if c.file not in broken]
    for file in sorted(broken):
        _log.warning(
            "%s does not parse; holding %d removed chunk(s) rather than reading "
            "them as deleted code",
            file, sum(1 for c in held if c.file == file),
        )
    return held
