"""Doc presence reconciliation — make a human deletion a real (soft) retire, and a
re-appearance an un-retire, without ever resurrecting a deleted node.

The bug this fixes: deleting a feature in the webview did nothing — the store kept
it and re-rendered it back ("deleted nodes come back after save"). The old code
refused to treat a *doc-vs-store* absence as a delete, and for good reason: the
store legitimately holds features the human's doc hasn't caught up to (an agent /
MCP-added feature isn't in the last-authored doc), so "absent from the doc → retire"
would wrongly tombstone agent work.

The safe signal is **doc-vs-*previous*-doc**: a feature that WAS in the last
rendered human doc (``tree.doc.json``) and is now gone is a genuine human deletion.
An agent-added feature is never falsely retired — it only enters the tracked set
once it has actually appeared in a rendered doc, so it can only be "removed" by a
human who saw it and deleted it.

The retire is **soft + detach-only** (KTD2 destructive asymmetry): the feature is
tombstoned (recoverable, never hard-deleted) and its bindings are detached so the
code is freed for re-homing, but NO code-deletion directive is queued. Re-adding the
node (undo / re-author) un-retires it. The previous fid set is persisted in
``.codoc/doc-fids.json``.
"""
from __future__ import annotations

from pathlib import Path

from codoc.codoc_file.doc_parse import parse_doc_file
from codoc.loop.fsio import atomic_write_json, read_json
from codoc.store.db import Store

DOC_FIDS_FILENAME = "doc-fids.json"


def _path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / DOC_FIDS_FILENAME


def read_doc_fids(codoc_dir: str | Path) -> set[str]:
    """The fid set of the last rendered human doc. Tolerant: missing/corrupt → empty
    (so the first pass detects no deletions)."""
    data = read_json(_path(codoc_dir), default={})
    fids = data.get("fids") if isinstance(data, dict) else None
    return set(fids) if isinstance(fids, list) else set()


def write_doc_fids(codoc_dir: str | Path, fids: set[str]) -> None:
    atomic_write_json(_path(codoc_dir), {"version": 1, "fids": sorted(fids)})


def reconcile_doc_presence(store: Store, codoc_dir: str | Path) -> tuple[int, int]:
    """Soft-retire features the human removed from the doc since the last pass, and
    un-retire ones that re-appeared. Returns ``(retired, unretired)``.

    Safe-by-construction: only fids that were in the *previous* doc and are now
    absent are retired — so an agent-added feature not yet in the human doc is never
    touched. A no-op (returns ``(0, 0)``) when there is no authoritative doc yet
    (CLI-only repo, or first run before any webview edit) or the parse errored — the
    guard against ever mass-retiring from a partial/unreadable doc.
    """
    parsed = parse_doc_file(codoc_dir)
    if parsed is None or parsed.errors or not parsed.nodes:
        return (0, 0)  # no authoritative doc / unreadable → never infer deletions

    current = {n.id for n in parsed.nodes if n.id}
    prev = read_doc_fids(codoc_dir)
    live = {f.id for f in store.list_features()}                 # active only
    retired_ids = {f.id for f in store.list_features(include_retired=True)} - live

    retired = 0
    for fid in (prev - current) & live:
        # Soft, detach-only retire — recoverable, never deletes code.
        store.retire_feature(fid)
        for b in store.bindings_for_feature(fid):
            store.delete_binding(b.file, b.symbol_path)
        retired += 1

    unretired = 0
    for fid in current & retired_ids:
        store.unretire_feature(fid)  # the node re-appeared (undo / re-author)
        unretired += 1

    write_doc_fids(codoc_dir, current)
    return (retired, unretired)
