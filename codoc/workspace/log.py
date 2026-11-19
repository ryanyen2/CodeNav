"""Session logs: the truth (docs/concurrency.md, Files and Replay).

Layout under ``<repo>/.codoc``, all tracked:

    log/<session>.jsonl          one file per applier session
    log/snapshot-<hlc>.jsonl     a folded state plus the frontier it folded
    log/archive/                 logs folded into a snapshot; replay does not read them

A session log has one writer, the applier that opened the session (the daemon,
or a writer applying with no daemon), and is only appended to. Its first line
is a header ``{"header": 1, "session": …, "opened": <hlc>}``; every later line
is one op as recorded (canonical payload, inverse, conflict), plus ``sub``
when the op answers a client submission.

``read_session`` validates before returning anything: header present, ids of
the form ``<session>:<seq>`` with ``seq`` strictly increasing from 1, ``at``
well-formed and non-decreasing. A file that fails is reported and skipped
whole; a partial read would put the store in a state no log describes.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from codoc.calculus.ops import Op, in_frontier
from codoc.workspace.clock import is_valid
from codoc.workspace.layout import ARCHIVE_DIR, LOG_DIR, SNAPSHOT_PREFIX


@dataclass
class LogProblem:
    path: str
    line: int
    reason: str


@dataclass
class SessionFile:
    path: Path
    session: str
    header: dict
    ops: list[Op]
    problems: list[LogProblem] = field(default_factory=list)
    torn_tail: int | None = None       # byte offset of an unreadable last line; the prefix is kept
    # (client, cid) -> the first op logged for that submission
    subs: dict[tuple[str, str], str] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return not self.problems or (self.torn_tail is not None and len(self.problems) == 1)


def log_dir(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / LOG_DIR


# ── reading ──────────────────────────────────────────────────────────────────

def read_session(path: str | Path) -> SessionFile:
    path = Path(path)
    problems: list[LogProblem] = []
    ops: list[Op] = []
    header: dict = {}
    session = path.stem
    with path.open("r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    if not lines:
        return SessionFile(path, session, {}, [], [LogProblem(str(path), 0, "empty")])
    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError as e:
        return SessionFile(path, session, {}, [], [LogProblem(str(path), 1, f"bad header: {e}")])
    if header.get("header") != 1 or "session" not in header:
        return SessionFile(path, session, header, [], [LogProblem(str(path), 1, "missing header")])
    session = header["session"]
    expected_seq = 1
    last_at = ""
    torn: int | None = None
    body = [(n, line) for n, line in enumerate(lines[1:], start=2) if line.strip()]
    subs: dict[tuple[str, str], str] = {}
    for k, (n, line) in enumerate(body):
        try:
            d = json.loads(line)
            op = Op.from_json(d)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            problems.append(LogProblem(str(path), n, f"unreadable op: {e}"))
            if k == len(body) - 1:
                # A crash mid-write left a torn last line: the prefix is a
                # state the applier was in, so it stays; the caller truncates.
                torn = len("\n".join(lines[:n - 1]).encode("utf-8")) + (1 if n > 1 else 0)
            break
        sess, _, seq = op.id.rpartition(":")
        if sess != session or not seq.isdigit() or int(seq) != expected_seq:
            problems.append(LogProblem(str(path), n, f"id {op.id!r} out of sequence (expected {session}:{expected_seq})"))
            break
        if not is_valid(op.at) or op.at < last_at:
            problems.append(LogProblem(str(path), n, f"clock {op.at!r} not monotone"))
            break
        expected_seq += 1
        last_at = op.at
        ops.append(op)
        sub = d.get("sub")
        if isinstance(sub, list) and len(sub) == 2 and all(isinstance(x, str) for x in sub):
            subs.setdefault((sub[0], sub[1]), op.id)
    if problems and torn is None:
        ops, subs = [], {}
    return SessionFile(path, session, header, ops, problems, torn, subs)


def truncate_torn(f: SessionFile) -> bool:
    """Cut a torn last line off a session file so every later rebuild agrees
    with this one. Returns whether anything was cut."""
    if f.torn_tail is None:
        return False
    with f.path.open("r+b") as fh:
        fh.truncate(f.torn_tail)
    f.torn_tail, f.problems = None, []
    return True


def list_sessions(codoc_dir: str | Path) -> list[Path]:
    d = log_dir(codoc_dir)
    if not d.exists():
        return []
    return sorted(p for p in d.glob("*.jsonl") if not p.name.startswith(SNAPSHOT_PREFIX))


def list_archived(codoc_dir: str | Path) -> list[Path]:
    """The session files a fold moved aside. Nothing reads them in the ordinary
    case (that is what folding them means), but a merge of two folds has to."""
    d = log_dir(codoc_dir) / ARCHIVE_DIR
    return sorted(d.glob("*.jsonl")) if d.is_dir() else []


def list_snapshots(codoc_dir: str | Path) -> list[Path]:
    d = log_dir(codoc_dir)
    if not d.exists():
        return []
    return sorted(d.glob(f"{SNAPSHOT_PREFIX}*.jsonl"))


def replay_order(files: list[SessionFile], exclude: dict | None = None) -> list[Op]:
    """All ops from the given sessions in ``(at, session, seq)`` order, skipping
    those inside ``exclude`` (a snapshot's frontier)."""
    out: list[tuple[str, str, int, Op]] = []
    for f in files:
        for op in f.ops:
            if exclude is not None and in_frontier(op.id, exclude):
                continue
            sess, _, seq = op.id.rpartition(":")
            out.append((op.at, sess, int(seq), op))
    out.sort(key=lambda t: (t[0], t[1], t[2]))
    return [t[3] for t in out]


# ── writing ──────────────────────────────────────────────────────────────────

class SessionWriter:
    """Append-only writer for one session log, held by the applier that
    opened the session until it rotates or exits.

    The file appears on the first op, not on open: log files are tracked, and
    ``codoc rebuild`` and ``codoc snapshot`` each open a writing workspace and
    write nothing, which would otherwise leave a header-only file for git to
    carry."""

    def __init__(self, codoc_dir: str | Path, session: str, opened: str) -> None:
        self.dir = log_dir(codoc_dir)
        self.path = self.dir / f"{session}.jsonl"
        self.session = session
        self._header = {"header": 1, "session": session, "opened": opened}
        self._fh = None

    def _open(self):
        if self._fh is None:
            self.dir.mkdir(parents=True, exist_ok=True)
            fresh = not self.path.exists()
            self._fh = self.path.open("a", encoding="utf-8")
            if fresh:
                self._fh.write(json.dumps(self._header) + "\n")
        return self._fh

    def append(self, op: Op, sub: tuple[str, str] | None = None) -> None:
        """Write one op to the OS; ``sync`` makes a batch durable. ``sub`` is
        the ``(client, cid)`` of the submission the op answers."""
        rec = op.to_json()
        if sub is not None:
            rec["sub"] = list(sub)
        fh = self._open()
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()

    def sync(self) -> None:
        if self._fh is not None:
            os.fsync(self._fh.fileno())

    def intact(self) -> bool:
        """Whether ``path`` still names the file this writer appends to. Git
        removes or replaces files under it (``stash -u``, ``clean``, a
        checkout, a discard, a fold that archived it), and an op appended to
        the old handle would reach no rebuild. True before the first op: there
        is no file to lose."""
        if self._fh is None:
            return True
        try:
            return os.path.samestat(os.stat(self.path), os.fstat(self._fh.fileno()))
        except OSError:
            return False

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
