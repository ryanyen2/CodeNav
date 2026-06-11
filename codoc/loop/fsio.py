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
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


def atomic_write_text(dest: str | Path, content: str) -> None:
    """Write *content* to *dest* atomically (tmp file + rename)."""
    dest = Path(dest)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(dest)


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
