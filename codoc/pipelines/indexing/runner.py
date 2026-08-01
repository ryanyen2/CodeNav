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
* **LanceDB upkeep** — Lance is copy-on-write: every committed pass appends a
  new table version and fragments, and nothing prunes them (a repo measured
  256MB / 4,253 versions for 22MB of live data before this). Each pass ends
  with ``optimize(cleanup_older_than=30min)`` — ~40ms steady-state.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import timedelta
from pathlib import Path


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
            have = bool(json.loads(meta_file.read_text()).get("embed_chunks"))
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
            meta_file.write_text(json.dumps({"embed_chunks": want}))
        except OSError:
            import logging

            logging.getLogger(__name__).warning(
                "codoc: could not record the embed flag at %s — the index may "
                "rebuild again next pass", meta_file)
    return wiped


def _maintain_table(codoc_path: Path) -> None:
    """Compact fragments + prune old versions after a pass (best-effort)."""
    from codoc.pipelines.indexing import reader

    async def _opt() -> None:
        tbl = await reader._open_table(codoc_path)
        # Not zero retention: concurrent readers (the IDE's daemon, a CLI
        # one-shot) may hold a just-superseded version open (lancedb#3086).
        await tbl.optimize(cleanup_older_than=timedelta(minutes=30))

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

    os.environ["COCOINDEX_DB"] = str(_cocoindex_db_path(codoc_path))
    os.environ["CODOC_LANCE_PATH"] = str(_lance_path(codoc_path))
    os.environ["CODOC_INDEX_SOURCE"] = str(Path(sourcedir).resolve())

    from codoc.pipelines.indexing.cocoindex_app import make_app

    app = make_app(Path(sourcedir).resolve())
    app.update_blocking(report_to_stdout=report_to_stdout)

    _maintain_table(codoc_path)
