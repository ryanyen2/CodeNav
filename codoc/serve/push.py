"""The SSE channel to a browser: the same messages the extension host posts to
its webview (``projection``, ``status``, ``acks``), so one bundle serves both.
A snapshot on connect; then a message whenever the file behind it changed and
its bytes differ (a no-op republish fans out to nobody)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from codoc.substrate import log as L

PROJECTION, STATUS, CONFIG, HISTORY = "projection.json", "status.json", "config.json", "history.json"


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def status_message(codoc_dir: Path, actor: str = "") -> dict | None:
    """The status the editor reads, plus the two facts only the host knows:
    the tree's language and who this viewer is (undo inverts their batches)."""
    st = _read(codoc_dir / STATUS)
    if st is None:
        return None
    try:
        status = json.loads(st)
        cfg = json.loads(_read(codoc_dir / CONFIG) or "{}")
    except ValueError:
        return None
    return {"kind": "status", "status": {**status, "doc_language": cfg.get("doc_language", "en"), "actor": actor}}


def history_message(codoc_dir: str | Path) -> dict | None:
    raw = _read(Path(codoc_dir) / HISTORY)
    try:
        return {"kind": "history", "history": json.loads(raw)} if raw else None
    except ValueError:
        return None


class Feed:
    """Per-connection state: what was last sent, and how far into the viewer's
    ack file we have read."""

    def __init__(self, codoc_dir: str | Path, client: str) -> None:
        self.dir = Path(codoc_dir)
        self.client = client
        self.actor = L.actor_for(client) or ""
        self.sent: dict[str, str] = {}
        ack = L.inbox_dir(self.dir) / f"{client}.ack.jsonl"
        self.ack_offset = ack.stat().st_size if ack.exists() else 0

    def pending(self) -> list[dict]:
        out: list[dict] = []
        proj = _read(self.dir / PROJECTION)
        if proj is not None and proj != self.sent.get(PROJECTION):
            self.sent[PROJECTION] = proj
            try:
                out.append({"kind": "projection", "proj": json.loads(proj), "realizing": []})
            except ValueError:
                pass
        st = status_message(self.dir, self.actor)
        key = json.dumps(st, sort_keys=True) if st else ""
        if st and key != self.sent.get(STATUS):
            self.sent[STATUS] = key
            out.append(st)
        acks = self._acks()
        if acks:
            out.append({"kind": "acks", "acks": acks})
        return out

    def _acks(self) -> list[dict]:
        path = L.inbox_dir(self.dir) / f"{self.client}.ack.jsonl"
        if not path.exists():
            return []
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


async def event_source(codoc_dir: str | Path, client: str, *, is_disconnected=None, poll_s: float = 0.5):
    """Async generator of SSE events; polls the few files it cares about (a
    watch on ``.codoc`` would also wake on every index write)."""
    feed = Feed(codoc_dir, client)
    while True:
        for m in feed.pending():
            yield {"event": m["kind"], "data": json.dumps(m, ensure_ascii=False)}
        if is_disconnected is not None and await is_disconnected():
            return
        await asyncio.sleep(poll_s)
