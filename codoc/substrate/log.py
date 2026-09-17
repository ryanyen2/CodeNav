"""Session logs and the client inbox (spec §7.1, §7.4, `04-version-control.md`).

Layout under ``<repo>/.codoc``:

    log/<session>.jsonl          the truth; one file per daemon session; tracked
    log/snapshot-<hlc>.jsonl     a folded state plus the frontier it folded; tracked
    log/archive/                 logs folded into a snapshot; tracked, replay-ignored
    inbox/<client>.jsonl         client → daemon submissions; ignored
    inbox/<client>.ack.jsonl     daemon → client acknowledgements; ignored

A session log is written by exactly one daemon and only appended to. Its first
line is a header ``{"header": 1, "session": …, "opened": <hlc>, "node": …}``;
every later line is one op as recorded (canonical payload, inverse, conflict).

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

from codoc.calculus.ops import Op
from codoc.substrate.clock import is_valid

LOG_DIR = "log"
INBOX_DIR = "inbox"
ARCHIVE_DIR = "archive"
SNAPSHOT_PREFIX = "snapshot-"


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

    @property
    def valid(self) -> bool:
        return not self.problems


def log_dir(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / LOG_DIR


def inbox_dir(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / INBOX_DIR


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
    for n, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            op = Op.from_json(d)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            problems.append(LogProblem(str(path), n, f"unreadable op: {e}"))
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
    if problems:
        ops = []
    return SessionFile(path, session, header, ops, problems)


def list_sessions(codoc_dir: str | Path) -> list[Path]:
    d = log_dir(codoc_dir)
    if not d.exists():
        return []
    return sorted(p for p in d.glob("*.jsonl") if not p.name.startswith(SNAPSHOT_PREFIX))


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
            sess, _, seq = op.id.rpartition(":")
            if exclude is not None and int(seq) <= exclude.get(sess, -1):
                continue
            out.append((op.at, sess, int(seq), op))
    out.sort(key=lambda t: (t[0], t[1], t[2]))
    return [t[3] for t in out]


# ── writing ──────────────────────────────────────────────────────────────────

class SessionWriter:
    """Append-only writer for one session log. Opened by the daemon; closed on
    exit, commit, or HEAD change."""

    def __init__(self, codoc_dir: str | Path, session: str, node: str, opened: str) -> None:
        self.dir = log_dir(codoc_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{session}.jsonl"
        self.session = session
        self._fh = None
        if not self.path.exists():
            with self.path.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps({"header": 1, "session": session, "opened": opened, "node": node}) + "\n")
        self._fh = self.path.open("a", encoding="utf-8")

    def append(self, op: Op) -> None:
        assert self._fh is not None
        self._fh.write(json.dumps(op.to_json(), ensure_ascii=False) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


# ── inbox ────────────────────────────────────────────────────────────────────

@dataclass
class Submission:
    """One client line: a client-side id plus an op body. The daemon mints the
    OpId, stamps the actor from the client's identity, applies, logs, acks."""
    cid: str
    kind: str
    target: dict
    payload: dict
    batch: str = ""
    plan: str | None = None
    base: dict = field(default_factory=dict)
    caused_by: str | None = None


def submit(codoc_dir: str | Path, client: str, subs: list[Submission]) -> Path:
    """Append submissions to this client's inbox file (O_APPEND; each line is
    one small write so concurrent writers interleave whole lines)."""
    d = inbox_dir(codoc_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{client}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for s in subs:
            fh.write(json.dumps(s.__dict__, ensure_ascii=False) + "\n")
    return path


def read_inbox(codoc_dir: str | Path, client: str, offset: int = 0) -> tuple[list[Submission], int]:
    """Submissions after byte ``offset``, and the new offset."""
    path = inbox_dir(codoc_dir) / f"{client}.jsonl"
    if not path.exists():
        return [], offset
    out: list[Submission] = []
    with path.open("rb") as fh:
        fh.seek(offset)
        data = fh.read()
    end = offset + len(data)
    for line in data.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            out.append(Submission(**{k: d.get(k) for k in Submission.__dataclass_fields__ if k in d}))
        except (json.JSONDecodeError, TypeError):
            continue   # a torn or foreign line; the client resubmits (idempotent by cid)
    return out, end


def clients(codoc_dir: str | Path) -> list[str]:
    d = inbox_dir(codoc_dir)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.jsonl") if not p.name.endswith(".ack.jsonl"))


def ack(codoc_dir: str | Path, client: str, cid: str, op: Op) -> None:
    path = inbox_dir(codoc_dir) / f"{client}.ack.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"cid": cid, "op": op.id, "conflict": op.conflict}) + "\n")
