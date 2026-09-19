"""The SSE channel to a browser: the same messages the extension host posts to
its webview (``projection``, ``status``, ``acks``), so one bundle serves both.
A snapshot on connect; then a message whenever the file behind it changed and
its bytes differ (a no-op republish fans out to nobody)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from codoc.substrate import log as L
from codoc.substrate.workspace import NONE, Roles

PROJECTION, STATUS, CONFIG, HISTORY = "projection.json", "status.json", "config.json", "history.json"


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _stamp(path: Path) -> tuple[float, int] | None:
    """A file's mtime and size: what changed, without reading it. Ten viewers
    of a large projection would otherwise read it all, twice a second, each."""
    try:
        st = path.stat()
        return (st.st_mtime, st.st_size)
    except OSError:
        return None


def status_message(codoc_dir: Path, actor: str = "") -> dict | None:
    """The status the editor reads, plus the three facts only the host knows:
    the tree's language, who this viewer is (undo inverts their batches), and
    what they may write — a contributor's edits ride as a plan of theirs, so
    the page must not offer them a verdict or a hand-off on somebody else's."""
    st = _read(codoc_dir / STATUS)
    if st is None:
        return None
    try:
        status = json.loads(st)
        cfg = json.loads(_read(codoc_dir / CONFIG) or "{}")
    except ValueError:
        return None
    from codoc.substrate.workspace import OWNER, Roles
    role = Roles.from_config(cfg).role(actor) if actor else OWNER
    return {"kind": "status", "status": {**status, "doc_language": cfg.get("doc_language", "en"),
                                         "actor": actor, "role": role}}


def history_message(codoc_dir: str | Path) -> dict | None:
    raw = _read(Path(codoc_dir) / HISTORY)
    try:
        return {"kind": "history", "history": json.loads(raw)} if raw else None
    except ValueError:
        return None


REPLAY_ACKS = 200


class Feed:
    """Per-connection state: what was last sent, and how far into the viewer's
    ack file we have read. A new connection replays the last few acks rather
    than starting at the end: an EventSource reconnects on its own, and an ack
    written in that gap is the one telling the editor its op landed. The editor
    ignores a cid it does not know."""

    def __init__(self, codoc_dir: str | Path, client: str, replay: int = REPLAY_ACKS) -> None:
        self.dir = Path(codoc_dir)
        self.client = client
        self.actor = L.actor_for(client) or ""
        self.sent: dict[str, str] = {}
        self.stamps: dict[str, tuple[float, int] | None] = {}
        ack = L.inbox_dir(self.dir) / f"{client}.ack.jsonl"
        size = ack.stat().st_size if ack.exists() else 0
        self.ack_offset = size
        if size and replay:
            # Count back from the end of the last COMPLETE line, not from the
            # end of the file: a daemon caught mid-append leaves a half-written
            # tail, and subtracting the complete lines from the file's size
            # lands the offset that many bytes inside the oldest replayed line —
            # which then parses as garbage and takes its ack with it.
            data = ack.read_bytes()
            complete = data.split(b"\n")[:-1]
            whole = sum(len(line) + 1 for line in complete)
            self.ack_offset = whole - sum(len(line) + 1 for line in complete[-replay:])

    def pending(self) -> list[dict]:
        # Acks FIRST, which is the order on disk: the daemon acks during the
        # drain and publishes afterwards. A projection ahead of the ack that
        # releases a block is skipped for that block (the page is right about a
        # block it still holds), so sending it first made the hub the one
        # surface where that always happened. The editor recovers either way —
        # it re-reads a released block the projection's frontier covers — but
        # the two surfaces should not differ in the order they deliver.
        out: list[dict] = []
        acks = self._acks()
        if acks:
            out.append({"kind": "acks", "acks": acks})
        proj = self._if_changed(PROJECTION)
        if proj is not None and proj != self.sent.get(PROJECTION):
            self.sent[PROJECTION] = proj
            try:
                out.append({"kind": "projection", "proj": json.loads(proj), "realizing": []})
            except ValueError:
                pass
        # Status is two files and a roles table; this runs twice a second per
        # connection, so it is read only when one of them has been written.
        if self._moved(STATUS) or self._moved(CONFIG):
            st = status_message(self.dir, self.actor)
            key = json.dumps(st, sort_keys=True) if st else ""
            if st and key != self.sent.get(STATUS):
                self.sent[STATUS] = key
                out.append(st)
        return out

    def _if_changed(self, name: str) -> str | None:
        return None if not self._moved(name) else _read(self.dir / name)

    def _moved(self, name: str) -> bool:
        """Whether this file has been written since we last looked. A file that
        does not exist has no stamp, and is looked at every time."""
        stamp = _stamp(self.dir / name)
        if stamp is not None and stamp == self.stamps.get(name):
            return False
        self.stamps[name] = stamp
        return True

    def _acks(self) -> list[dict]:
        path = L.inbox_dir(self.dir) / f"{self.client}.ack.jsonl"
        if not path.exists():
            return []
        # A file shorter than where we stopped has been rotated (`rotate_acks`
        # trims to the newest lines at session open), not gone quiet. Reading
        # from the start again costs a replay of acks the page already has —
        # which is idempotent — where not reading leaves every block held.
        if path.stat().st_size < self.ack_offset:
            self.ack_offset = 0
        with path.open("rb") as fh:
            fh.seek(self.ack_offset)
            data = fh.read()
        nl = data.rfind(b"\n")
        if nl < 0:
            return []
        self.ack_offset += nl + 1
        out = []
        for line in data[:nl].splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out


async def event_source(codoc_dir: str | Path, client: str, *, is_disconnected=None,
                       still_allowed=None, poll_s: float = 0.5):
    """Async generator of SSE events; polls the few files it cares about (a
    watch on ``.codoc`` would also wake on every index write).

    The door is re-asked every tick, because a stream is a long-lived READ of
    the whole tree and the door was only checked at connect: a collaborator
    removed from the repository kept receiving every projection until they
    closed the tab, for as long as the browser held it open. Two questions —
    the tracked roles table, which is what a revocation writes and is asked
    here, and ``still_allowed``, which the app fills in with its own session
    check. A revoked viewer's stream ends; their next request meets the door.
    """
    feed = Feed(codoc_dir, client)
    actor = L.actor_for(client)
    while True:
        if still_allowed is not None and not still_allowed():
            return
        if actor and Roles.load(codoc_dir).role(actor) == NONE:
            return
        for m in feed.pending():
            yield {"event": m["kind"], "data": json.dumps(m, ensure_ascii=False)}
        if is_disconnected is not None and await is_disconnected():
            return
        await asyncio.sleep(poll_s)
