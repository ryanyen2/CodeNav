"""What changed in the code: a diff of the chunk index before and after an update.

``compute_changeset`` snapshots the ``code_chunks`` table, runs an incremental
``update_index``, snapshots again, keys both by ``symbol`` and compares
``fingerprint`` to classify every chunk as added, removed or modified. Every
file is compared: the update indexes every file that changed since the last
one, so a change left out of this diff would be in no later diff either.
With no table before the update, the change set is a baseline, compared with
the bindings instead (``compute_changeset``). Deterministic; relocations are
recognised later, by the reflect pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from codoc.index.reader import ChunkRow, has_current_table, read_all_chunks
from codoc.index.runner import update_index


@dataclass
class ChunkRef:
    """A chunk involved in a change. ``source`` is the new source for an added
    or modified chunk, and empty for a removed one.

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
    baseline: bool = False                              # no index before: compared with the bindings

    def touched_files(self) -> set[str]:
        return {c.file for c in (self.added + self.removed + self.modified)}


def compute_changeset(root_dir: str, codoc_dir: str, *, all_sources: bool = False,
                      bindings: dict | None = None) -> ChangeSet:
    """Diff the index before and after an incremental update.

    The snapshots leave the ``source`` column out, so the diff reads only
    identities and fingerprints. Source is then read for the files whose
    chunks changed: the added and modified chunks carry it, and so do those
    files' rows in ``rows``, which is all the graph re-extracts.
    ``all_sources`` reads it for every row instead, for a graph built whole.

    With no chunk table before the update (a fresh clone, since the index is
    not tracked, or an index an older codoc wrote), there is nothing to diff,
    and the change set is a baseline. The code is compared with ``bindings``
    (symbol to anything with a ``fingerprint``): a bound chunk whose
    fingerprint differs is modified, a bound symbol the code no longer defines
    is removed, and nothing is added: with no index there is no telling code
    written since the last pass from code a person detached, and offering
    every unbound chunk to the model would undo each detach. Every row
    carries its source, for a graph built whole.
    """
    baseline = not has_current_table(codoc_dir)
    old = dict(bindings or {}) if baseline else {r.symbol: r for r in read_all_chunks(codoc_dir, with_source=False)}
    update_index(root_dir, codoc_dir)
    light = read_all_chunks(codoc_dir, with_source=False)
    new = {r.symbol: r for r in light}
    compared = old.keys() if baseline else old.keys() | new.keys()
    changed = {s for s in compared if s not in old or s not in new or old[s].fingerprint != new[s].fingerprint}
    files = {(new.get(s) or old[s]).file for s in changed}
    if all_sources or baseline:
        rows = read_all_chunks(codoc_dir)
    else:
        rows = [r for r in light if r.file not in files] + read_all_chunks(codoc_dir, files=files)
    sourced = {r.symbol: r for r in rows if r.file in files}

    cs = ChangeSet(rows=rows, baseline=baseline)
    for symbol in sorted(changed):
        if symbol not in new:
            o = old[symbol]
            cs.removed.append(ChunkRef(symbol, o.fingerprint, o.types_hash))
            continue
        n = sourced[symbol]
        (cs.modified if symbol in old else cs.added).append(ChunkRef(symbol, n.fingerprint, n.types_hash, n.source))
    return cs
