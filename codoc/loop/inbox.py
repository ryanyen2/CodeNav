"""``.codoc/inbox.json`` — the verdict channel between the IDE and the loops.

Replacing the old in-text ``?``→``+``/``-`` accept/reject syntax: the IDE's
Accept/Reject actions append a verdict here, and Loop B / ``codoc sync`` drain it
(:func:`read_verdicts` → apply → :func:`clear`). Keeping it a tiny on-disk file
preserves codoc's no-server, file-only contract — the daemon already watches
``.codoc``, so a write here wakes it.

Schema (version 1)::

    {"version": 1, "verdicts": [{"event_id": "e-1a2b", "accept": true}, …]}

A verdict may additionally carry ``title`` / ``description`` overrides: the IDE's
ghost proposals are EDITABLE before acceptance, so "accept" can mean "accept, as I
amended it". Loop B (the sole verdict applier) applies the proposal with the edited
text in place of the proposed text. Absent fields mean "as proposed" — an old IDE's
plain verdicts are unchanged.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from codoc.loop.fsio import atomic_write_json, read_json

INBOX_FILENAME = "inbox.json"
# The IDE's verdict APPEND-LOG (one JSON line per Accept/Reject click). The
# extension host holds no cross-process lock, so its old read-modify-write of
# inbox.json could land inside drop_verdicts' locked read-modify-write and erase
# a verdict the drop was about to write back — a click silently lost. An append
# can't erase anything; the log is folded into inbox.json under the inbox lock
# by merge_host_verdicts (same shape as edits.host.jsonl → edits.json).
HOST_VERDICTS_FILENAME = "inbox.host.jsonl"


@dataclass
class Verdict:
    event_id: str
    accept: bool
    # The author edited the ghost before accepting (None = "as proposed"). Only
    # meaningful on an accept; a reject discards the proposal, edits and all.
    title: str | None = None
    description: str | None = None


def inbox_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / INBOX_FILENAME


def host_verdicts_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / HOST_VERDICTS_FILENAME


def _amendment(v: dict) -> dict:
    """The optional accept-time edits, normalized: absent/empty → not carried."""
    out: dict = {}
    for key in ("title", "description"):
        val = v.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val
    return out


def _read(codoc_dir: str | Path) -> list[Verdict]:
    """inbox.json only — no merge. The internal read every locked mutator uses."""
    data = read_json(inbox_path(codoc_dir), default={})
    out: list[Verdict] = []
    for v in data.get("verdicts", []):
        eid = v.get("event_id")
        if eid:
            out.append(Verdict(event_id=eid, accept=bool(v.get("accept")),
                               **_amendment(v)))
    return out


def merge_host_verdicts(codoc_dir: str | Path) -> None:
    """Fold the IDE's verdict append-log into inbox.json (idempotent, locked).

    Last line wins per event id — the same double-click dedup the old direct
    writer had. Unparseable lines (a torn final append from a crashed host) are
    skipped; everything parseable merges, then the log is consumed."""
    path = host_verdicts_path(codoc_dir)
    if not path.exists():
        return
    with _inbox_lock(codoc_dir):
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return
        merged: dict[str, Verdict] = {v.event_id: v for v in _read(codoc_dir)}
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                eid = str(d["event_id"])
                merged[eid] = Verdict(event_id=eid, accept=bool(d["accept"]),
                                      **_amendment(d))
            except (ValueError, KeyError, TypeError):
                continue
        if merged:
            _write(codoc_dir, list(merged.values()))
        try:
            path.unlink()
        except OSError:
            pass


def read_verdicts(codoc_dir: str | Path) -> list[Verdict]:
    # Merge-on-read: every consumer (Loop B's drain, the render's voted set, the
    # hook fallback) sees host clicks the moment it looks, without each caller
    # having to know the log exists. The stat is cheap; the merge only runs when
    # the IDE actually appended something.
    if host_verdicts_path(codoc_dir).exists():
        merge_host_verdicts(codoc_dir)
    return _read(codoc_dir)


def clear(codoc_dir: str | Path) -> None:
    path = inbox_path(codoc_dir)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


_inbox_locks: dict[str, object] = {}


def _inbox_lock(codoc_dir: str | Path):
    """Cached, reentrant FileLock guarding inbox.json read-modify-write.

    inbox.json gains a second concurrent writer once the ``codoc serve`` hub
    writes remote verdicts alongside the daemon's drain; an atomic write stops
    torn reads but not lost updates across two read-modify-write cycles, so the
    append/drop paths hold this lock across their read AND write."""
    from filelock import FileLock

    key = str(inbox_path(codoc_dir)) + ".lock"
    lock = _inbox_locks.get(key)
    if lock is None:
        lock = FileLock(key, timeout=5)
        _inbox_locks[key] = lock
    return lock


def drop_verdicts(codoc_dir: str | Path, event_ids: set[str]) -> None:
    """Remove only the named verdicts, leaving any others in the inbox.

    Used by Loop B — the sole verdict applier — to consume exactly the verdicts it
    processed this pass, so one appended mid-pass survives to the next instead of
    being destroyed unprocessed. (``codoc_await_verdicts`` no longer touches the
    inbox at all: it observes outcomes from the event ledger, and its daemonless
    fallback drains by running this same Loop B path.)
    """
    with _inbox_lock(codoc_dir):
        remaining = [v for v in read_verdicts(codoc_dir) if v.event_id not in event_ids]
        if remaining:
            _write(codoc_dir, remaining)
        else:
            clear(codoc_dir)


def append_verdict(codoc_dir: str | Path, event_id: str, accept: bool,
                   *, title: str | None = None,
                   description: str | None = None) -> Path:
    """Append a verdict (used by the CLI/tests; the IDE writes this file too)."""
    with _inbox_lock(codoc_dir):
        verdicts = read_verdicts(codoc_dir)
        verdicts.append(Verdict(event_id=event_id, accept=accept,
                                title=title, description=description))
        return _write(codoc_dir, verdicts)


def _write(codoc_dir: str | Path, verdicts: list[Verdict]) -> Path:
    dest = inbox_path(codoc_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(dest, {"version": 1, "verdicts": [
        {"event_id": v.event_id, "accept": v.accept,
         # Accept-time edits ride only when present, so a plain verdict's on-disk
         # shape (and every existing reader of it) is byte-identical to before.
         **({"title": v.title} if v.title else {}),
         **({"description": v.description} if v.description else {})}
        for v in verdicts
    ]})
    return dest
