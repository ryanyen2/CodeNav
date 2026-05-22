from __future__ import annotations
import time
from dataclasses import dataclass, field
from collections import OrderedDict
from typing import Optional

TTL_SECONDS = 30
MAX_ENTRIES = 500

@dataclass
class ActivityEntry:
    session_id: str
    file_path: str        # absolute path
    rel_path: str         # repo-relative path
    tool: str             # "PreToolUse:Edit", "PostToolUse:Write", etc.
    started_at: float     # time.monotonic()
    ended_at: Optional[float] = None
    feature_uuids: list[str] = field(default_factory=list)
    feature_slugs: list[str] = field(default_factory=list)

class LiveActivity:
    """In-memory ledger of current Claude Code tool-use activity per file."""

    def __init__(self):
        # Ordered so we can evict old entries from front
        self._entries: OrderedDict[tuple[str, str], ActivityEntry] = OrderedDict()

    def record(
        self,
        session_id: str,
        file_path: str,
        rel_path: str,
        tool: str,
        phase: str,  # "pre" | "post"
        feature_uuids: list[str] | None = None,
        feature_slugs: list[str] | None = None,
    ) -> ActivityEntry:
        """Record an activity event. Updates existing entry for the same (session, file)."""
        key = (session_id, rel_path)
        entry = ActivityEntry(
            session_id=session_id,
            file_path=file_path,
            rel_path=rel_path,
            tool=tool,
            started_at=time.monotonic(),
            feature_uuids=feature_uuids or [],
            feature_slugs=feature_slugs or [],
        )
        if phase == "post":
            entry.ended_at = time.monotonic()
        self._entries[key] = entry
        self._evict()
        return entry

    def get_active(self) -> list[ActivityEntry]:
        """Return non-expired entries."""
        now = time.monotonic()
        return [
            e for e in self._entries.values()
            if now - e.started_at < TTL_SECONDS
        ]

    def clear_session(self, session_id: str) -> None:
        dead = [k for k in self._entries if k[0] == session_id]
        for k in dead:
            del self._entries[k]

    def _evict(self) -> None:
        now = time.monotonic()
        while self._entries:
            key, entry = next(iter(self._entries.items()))
            if now - entry.started_at >= TTL_SECONDS:
                del self._entries[key]
            else:
                break
        # Hard cap
        while len(self._entries) > MAX_ENTRIES:
            self._entries.popitem(last=False)

# Module-level singleton
ledger = LiveActivity()
