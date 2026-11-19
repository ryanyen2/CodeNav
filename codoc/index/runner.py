"""Bring the code chunk index up to date.

``update_index`` runs the cocoindex app once against a source tree. It is
idempotent and cheap when nothing changed (cocoindex memoizes per file). It
then compacts the LanceDB table: Lance is copy-on-write, every pass appends a
table version and fragments, and nothing else prunes them.
"""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path


# How long a superseded table version is kept before optimize may reclaim it.
# Not zero: a concurrent reader (the daemon, a CLI one-shot) may hold a
# newly superseded version open (lancedb#3086). Not long either: the window is
# wall clock while passes arrive seconds apart, and a version younger than the
# window is not reclaimed, so the next pass reads and rewrites a bloated table
# (a 2000-chunk rename grows it from 2.9 MB to 24.6 MB). A read takes
# milliseconds, so a minute is ample headroom for the reader this protects.
_VERSION_RETENTION = timedelta(
    seconds=int(os.environ.get("CODOC_INDEX_RETENTION_S", "60"))
)


def _maintain_table(codoc_path: Path) -> None:
    """Compact fragments + prune old versions after a pass (best-effort)."""
    from codoc.index import reader

    async def _opt() -> None:
        tbl = await reader._open_table(codoc_path)
        await tbl.optimize(cleanup_older_than=_VERSION_RETENTION)

    try:
        reader._run(_opt())
    except Exception as exc:  # noqa: BLE001 (upkeep must never fail the pass)
        # But a persistently failing optimize means versions and fragments are
        # accumulating, or the table is corrupt, and that must be visible.
        import logging

        logging.getLogger(__name__).warning(
            "codoc: LanceDB optimize failed at %s (%s); index upkeep skipped "
            "this pass", codoc_path, exc)


def update_index(
    sourcedir: str | Path,
    codoc_dir: str | Path = ".codoc",
    *,
    report_to_stdout: bool = False,
) -> None:
    """Run the cocoindex code-indexing app once against *sourcedir*.

    Persists chunks to ``{codoc_dir}/lancedb`` and tracks memoization state in
    ``{codoc_dir}/cocoindex.db``. Safe to call repeatedly: files whose
    content is unchanged are skipped; killed runs resume from the last
    committed component.
    """
    codoc_path = Path(codoc_dir).resolve()
    codoc_path.mkdir(parents=True, exist_ok=True)

    from codoc.index.app import make_app

    app = make_app(Path(sourcedir).resolve(), codoc_path)
    app.update_blocking(report_to_stdout=report_to_stdout)

    _maintain_table(codoc_path)
