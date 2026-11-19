"""The ``.codoc/`` directory: what its files are called, and the file primitives
every writer of them shares.

Names are relative to the ``.codoc`` directory. ``config.json``, ``log/`` and
``media/`` are tracked in git; everything else is derived or a local channel
and can be deleted.

Primitives: ``atomic_write`` (tmp file, then rename), ``read_json`` (a missing
or corrupt file degrades to the caller's default), ``locked`` (an exclusive
``flock`` for a read-modify-write), ``hold_lock`` (the same lock, kept as
long as the caller keeps the descriptor open: the daemon's on ``watch.pid``,
the hub's on ``serve.lock``), and ``find_codoc_dir``.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

CODOC_DIR = ".codoc"

# Tracked.
CONFIG = "config.json"            # workspace settings: doc_language, roles
LOG_DIR = "log"                   # session logs: the truth
ARCHIVE_DIR = "archive"           # under log/: sessions a snapshot folded
SNAPSHOT_PREFIX = "snapshot-"
MEDIA_DIR = "media"               # files that image and pdf blocks reference

# Channels into the applier.
INBOX_DIR = "inbox"               # <client>.jsonl submissions and <client>.ack.jsonl answers
OFFSETS = "offsets.json"          # under inbox/: how far each client's file has been drained
ACTIVITY = "activity.json"        # the agent epoch and what the session touched

# Derived files the applier publishes.
EXPORT = "tree.codoc"             # the Markdown export
PROJECTION = "projection.json"    # what the editor and the hub render
HISTORY = "history.json"          # the timeline window
STATUS = "status.json"            # workspace state and the realizing lease
REALIZE = "realize.md"            # the queued directives, as the realizing agent reads them
GRAPH_CACHE = "cache/graph.json"  # the code graph

# Other local state.
HUB_PRS = "cache/hub-prs.json"    # hand-off → pull-request branch, kept by the hub
PIDFILE = "watch.pid"             # the daemon holds a lock on it and writes its pid in it
APPLIER_LOCK = "applier.lock"     # held for one apply-and-publish turn
SERVE_LOCK = "serve.lock"         # the hub that owns this checkout


# What git carries of `.codoc/`: the log, the config and the files media blocks
# reference. The export is derived and rewritten on every edit, so it is not.
GITIGNORE = ("# codoc writes this file. The log, the config and the media blocks reference\n"
             "# travel with the repo; the export, caches, channels and local state do not.\n"
             "*\n!.gitignore\n!config.json\n!log/\n!log/**\n!media/\n!media/**\n")


def write_gitignore(codoc_dir: str | Path) -> None:
    """Write ``.codoc/.gitignore``, once, at ``codoc init``. Once it is
    committed it is the repository's: a person may change what it keeps out."""
    atomic_write(Path(codoc_dir) / ".gitignore", GITIGNORE)


def find_codoc_dir(cwd: str) -> str | None:
    """Walk up from *cwd* to the first ancestor that contains ``.codoc``."""
    p = Path(cwd).resolve()
    for candidate in [p, *p.parents]:
        if (candidate / CODOC_DIR).is_dir():
            return str(candidate / CODOC_DIR)
    return None


def atomic_write(dest: str | Path, text: str) -> None:
    """Write *text* to *dest* through a tmp file in the same directory and
    ``os.replace``, so a reader never sees a half-written file.

    The tmp name is unique per writer: two processes writing the same file with
    a shared ``<name>.tmp`` race, and the second rename fails because the first
    took the tmp away. Same directory keeps the rename on one filesystem."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=f".{dest.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json(dest: str | Path, payload: Any) -> None:
    """``atomic_write`` of *payload* as indented JSON. Not ASCII-escaped: these
    files carry authored prose, and an escaped CJK character is six bytes a
    person cannot read in a diff."""
    atomic_write(dest, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def read_json(path: str | Path, default: Any = None) -> Any:
    """Read a JSON file; a missing or corrupt one returns *default*.

    A non-empty file that fails to parse, or whose top-level type differs from
    a non-None *default*, is renamed to ``<name>.corrupt-<ns>`` first. The next
    writer merges over what this returns, so without the rename it would
    overwrite the unreadable file and lose whatever it held."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except (OSError, UnicodeDecodeError) as exc:
        _log.warning("unreadable control file %s (%s); treating as empty", path, exc)
        return default
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        if text.strip():
            _quarantine(path, str(exc))
        return default
    if default is not None and not isinstance(parsed, type(default)):
        _quarantine(path, f"top-level {type(parsed).__name__}, expected {type(default).__name__}")
        return default
    return parsed


def _quarantine(path: Path, why: str) -> None:
    try:
        quarantine = path.with_name(f"{path.name}.corrupt-{time.time_ns()}")
        os.replace(path, quarantine)
        _log.warning("corrupt control file %s (%s); quarantined to %s and treating "
                     "as empty", path, why, quarantine.name)
    except OSError:
        _log.warning("corrupt control file %s (%s); treating as empty", path, why)


@contextmanager
def locked(path: str | Path, timeout: float | None = None):
    """Hold an exclusive ``flock`` on *path* for the block. The OS releases it
    if the holder dies. With a *timeout*, raises ``TimeoutError`` instead of
    waiting past it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        take_flock(fd, path, timeout)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def hold_lock(path: str | Path) -> int | None:
    """Take an exclusive ``flock`` on *path* and write this process's pid into
    it, for a person to read. Returns the open descriptor, which holds the
    lock until it is closed or the process ends; None, with nothing written,
    when another process holds it. Liveness is the lock, never the pid."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        take_flock(fd, path, timeout=1)
    except TimeoutError:
        os.close(fd)
        return None
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    return fd


def take_flock(fd: int, path: str | Path, timeout: float | None = None) -> None:
    """Take an exclusive ``flock`` on the open file *fd* (*path* names it in
    the error). With a *timeout*, raises ``TimeoutError`` instead of waiting
    past it."""
    if timeout is None:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"could not lock {path} within {timeout}s") from None
            time.sleep(0.01)
