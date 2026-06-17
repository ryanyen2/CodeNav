"""Shared file IO for `.codoc/` control files.

Every control file (`status.json`, `inbox.json`, `edits.json`, `realize.md`,
`activity.json`, …) is written atomically (tmp → rename) so a reader never
sees a half-written file, and read tolerantly (a missing or corrupt file
degrades to the caller's default instead of crashing a loop pass).

A dependency-free leaf module, importable from any layer without cycles.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


def atomic_write_text(dest: str | Path, content: str) -> None:
    """Write *content* to *dest* atomically: a PER-WRITER-UNIQUE tmp file in the same
    directory, then ``os.replace``.

    The unique name matters under concurrency. Two writers of the same control file —
    two ``codoc watch`` daemons on one repo, or a daemon racing an MCP reflection in
    another process — using a shared ``<name>.tmp`` would race: the first ``os.replace``
    renames the tmp away, and the second then crashes with ``FileNotFoundError`` (its
    tmp is gone). A unique tmp per writer lets them coexist — both renames are atomic,
    the last writer wins, and the file is never left half-written. Same directory keeps
    the rename on one filesystem (a cross-device rename is not atomic)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=f".{dest.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, dest)
    except BaseException:
        # Best-effort cleanup of the orphaned tmp; re-raise the original error.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(dest: str | Path, payload: Any, *, indent: int = 2) -> None:
    """Serialize *payload* as JSON and write it atomically."""
    atomic_write_text(dest, json.dumps(payload, indent=indent) + "\n")


def read_json(path: str | Path, default: Any = None) -> Any:
    """Read a JSON control file; a missing or corrupt file returns *default*.

    Corruption is logged (a corrupt inbox/edits file silently becoming "empty"
    would otherwise look like data loss with no trace).
    """
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _log.warning("unreadable control file %s (%s); treating as empty", path, exc)
        return default
