"""``.codoc/inbox.json`` — the verdict channel between the IDE and the loops.

Replacing the old in-text ``?``→``+``/``-`` accept/reject syntax: the IDE's
Accept/Reject actions append a verdict here, and Loop B / ``codoc sync`` drain it
(:func:`read_verdicts` → apply → :func:`clear`). Keeping it a tiny on-disk file
preserves codoc's no-server, file-only contract — the daemon already watches
``.codoc``, so a write here wakes it.

Schema (version 1)::

    {"version": 1, "verdicts": [{"event_id": "e-1a2b", "accept": true}, …]}
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

INBOX_FILENAME = "inbox.json"


@dataclass
class Verdict:
    event_id: str
    accept: bool


def inbox_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / INBOX_FILENAME


def read_verdicts(codoc_dir: str | Path) -> list[Verdict]:
    path = inbox_path(codoc_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    out: list[Verdict] = []
    for v in data.get("verdicts", []):
        eid = v.get("event_id")
        if eid:
            out.append(Verdict(event_id=eid, accept=bool(v.get("accept"))))
    return out


def clear(codoc_dir: str | Path) -> None:
    path = inbox_path(codoc_dir)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def append_verdict(codoc_dir: str | Path, event_id: str, accept: bool) -> Path:
    """Append a verdict (used by the CLI/tests; the IDE writes this file too)."""
    verdicts = read_verdicts(codoc_dir)
    verdicts.append(Verdict(event_id=event_id, accept=accept))
    return _write(codoc_dir, verdicts)


def _write(codoc_dir: str | Path, verdicts: list[Verdict]) -> Path:
    dest = inbox_path(codoc_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "verdicts": [{"event_id": v.event_id, "accept": v.accept} for v in verdicts]}
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, dest)
    return dest
