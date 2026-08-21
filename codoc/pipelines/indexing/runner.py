"""Synchronous orchestrator for the cocoindex code-indexing pipeline.

Codoc's bootstrap and reflective pipelines call :func:`update_index` to ensure
the LanceDB-backed chunk index is current before reading from it. Calls are
idempotent and cheap when nothing has changed (cocoindex memoization).

Two maintenance duties live here (cocoindex does neither itself):

* **Embed-flag reconciliation** — the table schema differs with
  ``CODOC_EMBED_CHUNKS`` (see :mod:`codoc.pipelines.indexing.schema`). The
  active flag is recorded in ``{codoc_dir}/index.meta.json``; on mismatch the
  LanceDB table + cocoindex memo state are wiped so the next pass rebuilds
  cleanly under the other schema (rare, explicit, self-healing).
* **The one open environment** — cocoindex holds ONE environment per process and
  an App registers itself by name inside it, so both the app and the environment
  are cached here rather than rebuilt: rebuilding the app lets a retained failure
  poison the process, and rebuilding the environment silently indexes the wrong
  workspace (see :func:`_app_for`).
* **LanceDB upkeep** — Lance is copy-on-write: every committed pass appends a
  new table version and fragments, and nothing prunes them (a repo measured
  256MB / 4,253 versions for 22MB of live data before this). Each pass ends
  with ``optimize(cleanup_older_than=30min)`` — ~40ms steady-state.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from datetime import timedelta
from pathlib import Path

#: the app name every workspace uses, so a memo key written by an earlier codoc
#: version still matches. Only a second workspace in the same process needs another.
_APP_NAME = "CodocIndex"

#: (sourcedir, codoc_dir, embed) → the App that indexes it. At most ONE entry: the
#: cocoindex environment it runs in is per-process, so holding a second workspace's
#: app would hold one that reads the wrong index (see :func:`_app_for`).
_apps: dict[tuple[str, str, bool], object] = {}
_apps_lock = threading.Lock()


def _lance_path(codoc_dir: Path) -> Path:
    return codoc_dir / "lancedb"


def _cocoindex_db_path(codoc_dir: Path) -> Path:
    return codoc_dir / "cocoindex.db"


def _meta_path(codoc_dir: Path) -> Path:
    return codoc_dir / "index.meta.json"


def _reconcile_embed_flag(codoc_path: Path) -> bool:
    """Resolve the embed flag and wipe the index when the schema must change.

    Resolution: an EXPLICIT ``CODOC_EMBED_CHUNKS`` wins; with the var unset the
    index's recorded state (``index.meta.json``) is authoritative — so a daemon
    and a CLI with different environments can't alternately wipe each other's
    index every pass. A missing meta beside an existing index means the index
    predates the flag (old always-embed schema): it rebuilds embedding-free,
    which also reclaims the accumulated vector bloat. The RESOLVED value is
    pinned back into the env so the cocoindex app (which reads the env) builds
    under the same schema this function decided on. Returns True on wipe.
    """
    from codoc.pipelines.indexing.schema import embed_chunks_requested

    meta_file = _meta_path(codoc_path)
    have: bool | None = None
    if meta_file.exists():
        try:
            have = bool(json.loads(meta_file.read_text(encoding="utf-8")).get("embed_chunks"))
        except (OSError, ValueError):
            have = None
    requested = embed_chunks_requested()
    want = requested if requested is not None else bool(have)
    os.environ["CODOC_EMBED_CHUNKS"] = "1" if want else "0"

    index_exists = _lance_path(codoc_path).exists() or _cocoindex_db_path(codoc_path).exists()
    wiped = False
    if index_exists and have != want:
        import logging

        logging.getLogger(__name__).warning(
            "codoc: rebuilding the chunk index at %s (embed_chunks %s -> %s) — "
            "derived state only; the store is untouched",
            codoc_path, have, want)
        shutil.rmtree(_lance_path(codoc_path), ignore_errors=True)
        shutil.rmtree(_cocoindex_db_path(codoc_path), ignore_errors=True)
        _cocoindex_db_path(codoc_path).unlink(missing_ok=True)
        wiped = True
    if have != want:
        try:
            meta_file.write_text(json.dumps({"embed_chunks": want}), encoding="utf-8")
        except OSError:
            import logging

            logging.getLogger(__name__).warning(
                "codoc: could not record the embed flag at %s — the index may "
                "rebuild again next pass", meta_file)
    return wiped


# How long a superseded table version is kept before optimize may reclaim it.
# Not zero: a concurrent reader (the IDE's daemon, a CLI one-shot) may hold a
# just-superseded version open (lancedb#3086). But 30 minutes was far too long,
# because it is measured against WALL CLOCK while passes are measured against
# EDITS — and edits arrive seconds apart.
#
# Measured on a rename of 2000 chunks: the table goes from 2.9 MB to 24.6 MB
# during the pass, and optimize then reclaims none of it (24.6 -> 25.0 MB)
# because every version it would drop is younger than the window. The next pass
# reads and rewrites the bloated table, leaves more debris, and so on. That is
# the compounding behind a rename pass costing 21.5x the time for 6.7x the
# chunks, and behind an earlier "283 MB pathology" in the same code.
#
# A read takes milliseconds, so a minute is already orders of magnitude of
# headroom for the reader this protects.
_VERSION_RETENTION = timedelta(
    seconds=int(os.environ.get("CODOC_INDEX_RETENTION_S", "60"))
)


def _maintain_table(codoc_path: Path) -> None:
    """Compact fragments + prune old versions after a pass (best-effort)."""
    from codoc.pipelines.indexing import reader

    async def _opt() -> None:
        tbl = await reader._open_table(codoc_path)
        await tbl.optimize(cleanup_older_than=_VERSION_RETENTION)

    try:
        reader._run(_opt())
    except Exception as exc:  # noqa: BLE001 — upkeep must never fail the pass…
        # …but a persistently failing optimize means versions/fragments are
        # accumulating again (the 283MB pathology) or the table is corrupt —
        # that must be visible, not silent.
        import logging

        logging.getLogger(__name__).warning(
            "codoc: LanceDB optimize failed at %s (%s) — index upkeep skipped "
            "this pass", codoc_path, exc)


def _app_for(sourcedir: Path, codoc_path: Path, embed: bool):
    """The cocoindex App for one workspace, built once and reused every pass.

    Rebuilding it per pass looked free and was not. A cocoindex App registers itself
    by NAME in a process-wide registry of weak references, so an app that is still
    referenced anywhere keeps the name taken — and a FAILED pass is referenced by its
    own traceback, which anything holding `exc_info` (a `logging.exception` call, a
    stored `sys.exc_info()`, a debugger) keeps alive. The next pass then died in the
    constructor with "An app named 'CodocIndex' is already registered in this
    environment", which is not the error anybody was looking at and does not clear
    until the process ends: one transient index failure permanently broke a daemon.

    Reuse fixes that at the root, and the name is why the fix cannot be a unique name
    per pass instead: the app name is part of cocoindex's memo key, so renaming makes
    every pass re-process every file (measured: "3 unchanged" becomes "3 added").

    **Only one workspace at a time.** Where each workspace's index LIVES is decided
    once, when cocoindex's single per-process environment enters its lifespan and
    reads ``CODOC_LANCE_PATH`` / ``COCOINDEX_DB`` — after that the paths are fixed for
    the process. A second workspace indexed in the same process (tests, a tool that
    walks several repos; not a daemon, which has one) therefore wrote its rows into
    the FIRST workspace's index and left its own empty, with no error anywhere:
    measured as `two []`, `three []` where each should have had its own rows. So a
    switch of workspace closes the environment first, and the next pass re-enters the
    lifespan against the new paths. Per-file memo state is in each workspace's own
    ``cocoindex.db`` and survives the round trip (measured: back to the first
    workspace is still "1 unchanged"); only the walk itself re-runs.
    """
    key = (str(sourcedir), str(codoc_path), embed)
    with _apps_lock:
        app = _apps.get(key)
        if app is not None and _open_state_intact(codoc_path):
            return app
        _release_environment()
        from codoc.pipelines.indexing.cocoindex_app import make_app

        try:
            app = make_app(sourcedir, app_name=_APP_NAME)
        except ValueError:
            # The name is still held by an app we no longer have — a traceback
            # somewhere keeps it alive. Index under a name derived from this
            # workspace: stable across processes, so it costs one rebuild, once.
            digest = hashlib.sha1(str(codoc_path).encode("utf-8")).hexdigest()[:8]
            app = make_app(sourcedir, app_name=f"{_APP_NAME}-{digest}")
        _apps[key] = app
        return app


def _open_state_intact(codoc_path: Path) -> bool:
    """Are the two things the environment opened still the ones on disk?

    The environment opened ``lancedb/`` and ``cocoindex.db`` when it entered its
    lifespan and holds those handles for as long as it lives. Deleting ``.codoc``
    from a shell (a re-init, a hand cleanup) leaves the handles valid and pointed at
    an unlinked directory, so the next pass reported success and wrote nothing:
    measured as an empty ``lancedb/`` and no rows, permanently, for that process.
    A missing path means the environment has to be reopened, not that the index is
    broken — the pass that finds it missing rebuilds it.
    """
    return _lance_path(codoc_path).exists() and _cocoindex_db_path(codoc_path).exists()


def _release_environment() -> None:
    """Close the cocoindex environment, so the next pass rebinds it to its workspace.

    Call with :data:`_apps_lock` held, having decided that whatever the environment
    is currently bound to is not what the next pass wants.
    """
    if not _apps:
        return
    _apps.clear()
    import cocoindex as coco

    coco.stop_blocking()


def _forget_app(codoc_path: Path) -> None:
    """Release the app and environment for a workspace whose index was just wiped.

    The environment holds an open connection to a LanceDB directory that no longer
    exists, so it has to be closed too — dropping the app alone leaves the next pass
    writing through a connection to a deleted path.
    """
    with _apps_lock:
        if any(key[1] == str(codoc_path) for key in _apps):
            _release_environment()


def update_index(
    sourcedir: str | Path,
    codoc_dir: str | Path = ".codoc",
    *,
    report_to_stdout: bool = False,
) -> None:
    """Run the cocoindex code-indexing app once against *sourcedir*.

    Persists chunks (+ embeddings when ``CODOC_EMBED_CHUNKS=1``) to
    ``{codoc_dir}/lancedb`` and tracks memoization state in
    ``{codoc_dir}/cocoindex.db``. Safe to call repeatedly: files whose
    fingerprint is unchanged are skipped; killed runs resume from the last
    committed component.
    """
    codoc_path = Path(codoc_dir).resolve()
    codoc_path.mkdir(parents=True, exist_ok=True)

    if _reconcile_embed_flag(codoc_path):
        from codoc.pipelines.indexing.reader import invalidate_cache

        invalidate_cache(codoc_path)
        _forget_app(codoc_path)

    os.environ["COCOINDEX_DB"] = str(_cocoindex_db_path(codoc_path))
    os.environ["CODOC_LANCE_PATH"] = str(_lance_path(codoc_path))
    os.environ["CODOC_INDEX_SOURCE"] = str(Path(sourcedir).resolve())

    from codoc.pipelines.indexing.schema import embed_chunks_enabled

    app = _app_for(Path(sourcedir).resolve(), codoc_path, embed_chunks_enabled())
    app.update_blocking(report_to_stdout=report_to_stdout)

    _maintain_table(codoc_path)
