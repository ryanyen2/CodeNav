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

# Inbox file name → the actor the daemon stamps. Locally the file name is the
# identity (any process that writes ide.jsonl is the person at the keyboard);
# the hub writes one file per GitHub login, stamped from its session cookie.
CLIENT_IDENTITY = {"ide": "human:local", "cli": "human:local", "mcp": "agent:claude-code"}
OFFSETS_FILE = "offsets.json"      # how far each client's inbox has been drained
ACK_KEEP = 2000                    # ack lines kept when a session opens


def hub_actor(login: str) -> str:
    """A remote contributor's actor. The ``gh:`` prefix is what keeps a GitHub
    login named ``local`` (or ``import``) from arriving as the maintainer's own
    identity, here and in the roles table."""
    return f"human:gh:{login}"


def actor_for(client: str) -> str | None:
    if client in CLIENT_IDENTITY:
        return CLIENT_IDENTITY[client]
    if client.startswith("hub-") and len(client) > 4:
        return hub_actor(client[4:])
    return None


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
    # Whether the writer said it was done with this file. Nothing may ARCHIVE
    # an open one: a fold that moved a file a live applier still holds would
    # send that applier's next op to `log/archive/`, which no rebuild reads.
    # (Git's `gc` packs only what no process is writing, for the same reason.)
    closed: bool = False

    @property
    def valid(self) -> bool:
        return not self.problems or (self.torn_tail is not None and len(self.problems) == 1)


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
    torn: int | None = None
    body = [(n, line) for n, line in enumerate(lines[1:], start=2) if line.strip()]
    closed = False
    for k, (n, line) in enumerate(body):
        try:
            d = json.loads(line)
            if "closed" in d and "id" not in d:
                closed = True         # the writer's trailer: this file is done
                continue
            op = Op.from_json(d)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            problems.append(LogProblem(str(path), n, f"unreadable op: {e}"))
            if k == len(body) - 1:
                # A crash mid-write left a torn last line: the prefix is a
                # state the daemon was in, so it stays; the caller truncates.
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
    if problems and torn is None:
        ops = []
    return SessionFile(path, session, header, ops, problems, torn, closed)


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
    case — that is what folding them means — but a merge of two folds has to."""
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
            sess, _, seq = op.id.rpartition(":")
            if exclude is not None and int(seq) <= exclude.get(sess, -1):
                continue
            out.append((op.at, sess, int(seq), op))
    out.sort(key=lambda t: (t[0], t[1], t[2]))
    return [t[3] for t in out]


# ── writing ──────────────────────────────────────────────────────────────────

class SessionWriter:
    """Append-only writer for one session log. Opened by the daemon; closed on
    exit, commit, or HEAD change.

    The file appears on the first op, not on open: log files are tracked, and
    ``codoc rebuild``, ``codoc snapshot`` and ``import-v1`` each open a writing
    workspace and write nothing. Three such runs used to leave three
    header-only files for git to carry."""

    def __init__(self, codoc_dir: str | Path, session: str, node: str, opened: str) -> None:
        self.dir = log_dir(codoc_dir)
        self.path = self.dir / f"{session}.jsonl"
        self.session = session
        self._header = {"header": 1, "session": session, "opened": opened, "node": node}
        self._fh = None

    def _open(self):
        if self._fh is None:
            self.dir.mkdir(parents=True, exist_ok=True)
            fresh = not self.path.exists()
            self._fh = self.path.open("a", encoding="utf-8")
            if fresh:
                self._fh.write(json.dumps(self._header) + "\n")
        return self._fh

    def append(self, op: Op) -> None:
        """Write one op to the OS; ``sync`` makes a batch durable."""
        fh = self._open()
        fh.write(json.dumps(op.to_json(), ensure_ascii=False) + "\n")
        fh.flush()

    def sync(self) -> None:
        if self._fh is not None:
            os.fsync(self._fh.fileno())

    def close(self) -> None:
        """Stamp the file closed and let it go. The stamp is what tells a fold
        this file may be archived: an open one belongs to a live applier."""
        if self._fh is not None:
            self._fh.write(json.dumps({"closed": 1}) + "\n")
            self.sync()
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
    """Append submissions to this client's inbox file: one ``O_APPEND`` write
    for the whole batch, so concurrent writers interleave whole lines."""
    d = inbox_dir(codoc_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{client}.jsonl"
    data = "".join(json.dumps(s.__dict__, ensure_ascii=False) + "\n" for s in subs).encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return path


@dataclass
class InboxLine:
    """One complete inbox line: a valid submission, or the error that makes it
    invalid (acked back so the client learns), and the offset just past it."""
    sub: Submission | None
    error: str | None
    end: int
    cid: str = ""


def read_inbox(codoc_dir: str | Path, client: str, offset: int = 0) -> list[InboxLine]:
    """Complete lines after byte ``offset``. An unterminated tail is left for
    the next read: a writer may be mid-line."""
    path = inbox_dir(codoc_dir) / f"{client}.jsonl"
    if not path.exists():
        return []
    with path.open("rb") as fh:
        # A client only ever appends; a file shorter than the remembered offset
        # is a new one under the same name, and it is read whole.
        fh.seek(offset if offset <= path.stat().st_size else 0)
        data = fh.read()
    out: list[InboxLine] = []
    pos = 0
    while (nl := data.find(b"\n", pos)) >= 0:
        raw, pos = data[pos:nl], nl + 1
        end = offset + pos
        if not raw.strip():
            continue
        try:
            d = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            out.append(InboxLine(None, f"unreadable: {e}", end))
            continue
        cid = d.get("cid") if isinstance(d, dict) else None
        err = _validate(d)
        if err:
            out.append(InboxLine(None, err, end, cid if isinstance(cid, str) else ""))
            continue
        out.append(InboxLine(Submission(**{k: d[k] for k in Submission.__dataclass_fields__ if k in d and d[k] is not None}),
                             None, end, cid))
    return out


def _validate(d) -> str | None:
    if not isinstance(d, dict):
        return "not an object"
    if not isinstance(d.get("cid"), str) or not d["cid"]:
        return "cid must be a non-empty string"
    if not isinstance(d.get("kind"), str):
        return "kind must be a string"
    for key in ("target", "payload"):
        if not isinstance(d.get(key), dict):
            return f"{key} must be an object"
    for key in ("batch", "plan", "caused_by"):
        if d.get(key) is not None and not isinstance(d[key], str):
            return f"{key} must be a string"
    if d.get("base") is not None and not isinstance(d["base"], dict):
        return "base must be an object"
    if d["kind"] == "ingest":
        if not isinstance(d["target"].get("bid"), str):
            return "ingest target.bid must be a string"
        for key in ("base_text", "new_md"):
            if not isinstance(d["payload"].get(key), str):
                return f"ingest payload.{key} must be a string"
    return None


def clients(codoc_dir: str | Path) -> list[str]:
    d = inbox_dir(codoc_dir)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.jsonl") if not p.name.endswith(".ack.jsonl"))


def ack(codoc_dir: str | Path, client: str, cid: str, op: Op | None = None, *, error: str | None = None,
        status: str | None = None) -> None:
    """Tell the client what became of ``cid``: the op it minted (and its
    conflict, if any), or the error that kept it from minting one. ``status``
    is for an outcome that is not a refusal — a whole-text write that asked for
    nothing mints no op and conflicts with nothing."""
    path = inbox_dir(codoc_dir) / f"{client}.ack.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"cid": cid, "op": op.id if op else "", "conflict": op.conflict if op else error}
    if status:
        rec["status"] = status
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def read_offsets(codoc_dir: str | Path) -> dict[str, int]:
    """How far into each client's inbox the last session read. Without it a
    daemon re-reads every submission ever made and leans on the ack file to
    recognise them, so neither file may ever be trimmed."""
    p = inbox_dir(codoc_dir) / OFFSETS_FILE
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): int(v) for k, v in d.items() if isinstance(v, int)} if isinstance(d, dict) else {}


def write_offsets(codoc_dir: str | Path, offsets: dict[str, int]) -> None:
    d = inbox_dir(codoc_dir)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (OFFSETS_FILE + ".tmp")
    tmp.write_text(json.dumps(offsets, sort_keys=True), encoding="utf-8")
    tmp.replace(d / OFFSETS_FILE)


def rotate_acks(codoc_dir: str | Path, keep: int = ACK_KEEP) -> None:
    """Trim each client's ack file to its newest lines. An ack answers one
    submission; once the remembered offset is past it nothing can RE-READ that
    line, and the only cost left is the read at every daemon start. Called at
    session open, while this process holds the workspace.

    This bounds 01 §7.4's rule — "a resubmitted client id is acked with the same
    OpId" — to the newest ``keep`` acks per client. What is left is a client
    that re-APPENDS an id it sent more than that many acks ago, which means it
    lost its own state and kept its counter; the editor mints ids under a
    per-page random prefix, so a page that restarts never collides with one."""
    d = inbox_dir(codoc_dir)
    if keep < 1 or not d.exists():
        return
    for p in d.glob("*.ack.jsonl"):
        lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
        if len(lines) <= keep:
            continue
        tmp = p.with_suffix(".tmp")
        tmp.write_text("".join(lines[-keep:]), encoding="utf-8")
        tmp.replace(p)


def acked(codoc_dir: str | Path) -> dict[str, dict[str, str]]:
    """Per client, every acked cid and the op it minted (``""`` when none):
    what the drain skips, and what a later provisional anchor may name."""
    out: dict[str, dict[str, str]] = {}
    d = inbox_dir(codoc_dir)
    if not d.exists():
        return out
    for p in d.glob("*.ack.jsonl"):
        client = p.name[: -len(".ack.jsonl")]
        with p.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    a = json.loads(line)
                    out.setdefault(client, {})[a["cid"]] = a.get("op") or ""
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
    return out
