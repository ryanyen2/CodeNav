"""client.py — a thin, injectable wrapper over the Notion API.

Everything the bridge needs from Notion goes through here: read the page's block
tree (paginated + recursively hydrated), make context-anchored content writes,
read comments, and read ``last_edited_time``. The wrapper is built around an
injected ``transport`` so unit tests run against a fake — no live token, no network.
The real transport adapts ``notion_client.Client`` (lazy-imported via the ``notion``
extra) to the same small method surface.

Concurrency posture (Notion exposes no etag / conditional write — see the plan KTD):
content writes use the markdown ``update_content`` command with a context-rich
``old_str``; if a concurrent edit changed the anchor, the API returns
``validation_error`` and we surface :class:`NotionConcurrencyError` so the caller
re-reads and re-plans, rather than clobbering. We never use ``replace_content``
(whole-page overwrite) for live sync.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from codoc.notion.config import NotionConfig

# Notion rate-limit / overload responses carry a Retry-After (integer seconds).
_RETRYABLE_STATUS = {429, 529}


class NotionAPIError(Exception):
    """A Notion API error. ``status`` is the HTTP status, ``code`` the Notion error
    code (e.g. ``rate_limited``, ``validation_error``), ``retry_after`` the seconds
    Notion asked us to wait (0 when none)."""

    def __init__(self, status: int, code: str = "", message: str = "", retry_after: float = 0):
        super().__init__(message or code or f"HTTP {status}")
        self.status = status
        self.code = code
        self.retry_after = retry_after


class NotionConcurrencyError(Exception):
    """A context-anchored write failed because the anchor text was not found (or was
    ambiguous) — a concurrent edit moved it. Re-read the page and re-plan."""


class Transport(Protocol):
    """The minimal Notion surface the wrapper uses (the seam tests fake)."""

    def list_block_children(self, block_id: str, start_cursor: str | None) -> dict: ...
    def update_page_markdown(self, page_id: str, commands: list[dict]) -> dict: ...
    def append_block_children(self, parent_id: str, children: list[dict]) -> dict: ...
    def delete_block(self, block_id: str) -> dict: ...
    def list_comments(self, block_id: str, start_cursor: str | None) -> dict: ...
    def retrieve_page(self, page_id: str) -> dict: ...


@dataclass
class _RateLimiter:
    """A blocking token bucket (~``rate`` requests/sec, burst ``capacity``). Honors
    the Notion 3 req/sec ceiling. Clock + sleep are injected for deterministic tests."""
    rate: float = 3.0
    capacity: float = 3.0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    _tokens: float = field(default=0.0, init=False)
    _last: float | None = field(default=None, init=False)

    def acquire(self) -> None:
        now = self.clock()
        if self._last is None:
            self._tokens = self.capacity
        else:
            self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now
        if self._tokens < 1.0:
            wait = (1.0 - self._tokens) / self.rate
            self.sleep(wait)
            self._tokens = 0.0
            self._last = self.clock()
        else:
            self._tokens -= 1.0


class NotionClient:
    """Wrapper over an injected Notion ``transport`` with rate-limit + backoff."""

    def __init__(self, config: NotionConfig, transport: Transport, *,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 max_retries: int = 5):
        self._config = config
        self._t = transport
        self._sleep = sleep
        self._max_retries = max_retries
        self._limiter = _RateLimiter(sleep=sleep, clock=clock)

    # ── core request path: rate-limit + Retry-After backoff ──────────────────
    def _call(self, method: str, *args, **kwargs):
        fn = getattr(self._t, method)
        attempt = 0
        while True:
            self._limiter.acquire()
            try:
                return fn(*args, **kwargs)
            except NotionAPIError as exc:
                if exc.status in _RETRYABLE_STATUS and attempt < self._max_retries:
                    attempt += 1
                    self._sleep(exc.retry_after or 1.0)
                    continue
                raise

    # ── reads ────────────────────────────────────────────────────────────────
    def get_block_tree(self, page_id: str | None = None) -> list[dict]:
        """The page's block list with children recursively hydrated under ``children``
        (the shape :func:`codoc.notion.parse.parse_blocks` expects)."""
        root = page_id or self._config.page_id
        return self._children_of(root)

    def _children_of(self, block_id: str) -> list[dict]:
        out: list[dict] = []
        cursor: str | None = None
        while True:
            page = self._call("list_block_children", block_id, cursor)
            for block in page.get("results", []):
                if block.get("has_children"):
                    block = {**block, "children": self._children_of(block["id"])}
                out.append(block)
            if not page.get("has_more"):
                break
            cursor = page.get("next_cursor")
        return out

    def list_comments(self, block_id: str) -> list[dict]:
        out: list[dict] = []
        cursor: str | None = None
        while True:
            page = self._call("list_comments", block_id, cursor)
            out.extend(page.get("results", []))
            if not page.get("has_more"):
                break
            cursor = page.get("next_cursor")
        return out

    def last_edited_time(self, page_id: str | None = None) -> str:
        page = self._call("retrieve_page", page_id or self._config.page_id)
        return page.get("last_edited_time") or ""

    # ── writes ────────────────────────────────────────────────────────────────
    def update_content(self, edits: list[tuple[str, str]], page_id: str | None = None) -> dict:
        """Apply context-anchored search/replace edits. Each ``(old_str, new_str)``
        becomes an ``update_content`` command. A missing/ambiguous anchor surfaces as
        :class:`NotionConcurrencyError` (a concurrent edit moved it)."""
        commands = [{"old_str": old, "new_str": new} for old, new in edits]
        try:
            return self._call("update_page_markdown", page_id or self._config.page_id, commands)
        except NotionAPIError as exc:
            if exc.code == "validation_error":
                raise NotionConcurrencyError(
                    "anchor text not found or ambiguous — the page changed since last read"
                ) from exc
            raise

    def append_children(self, parent_id: str, children: list[dict]) -> dict:
        return self._call("append_block_children", parent_id, children)

    def delete_block(self, block_id: str) -> dict:
        return self._call("delete_block", block_id)


def build_real_transport(config: NotionConfig):  # pragma: no cover - needs the extra + a token
    """Adapt ``notion_client.Client`` to the :class:`Transport` surface. Lazy-imported
    so the base CLI never requires the ``notion`` extra."""
    from notion_client import Client

    client = Client(auth=config.token, notion_version=config.notion_version)

    class _RealTransport:
        def list_block_children(self, block_id, start_cursor):
            return client.blocks.children.list(block_id=block_id, start_cursor=start_cursor)

        def update_page_markdown(self, page_id, commands):
            return client.request(
                path=f"pages/{page_id}/markdown", method="PATCH", body={"commands": commands})

        def append_block_children(self, parent_id, children):
            return client.blocks.children.append(block_id=parent_id, children=children)

        def delete_block(self, block_id):
            return client.blocks.delete(block_id=block_id)

        def list_comments(self, block_id, start_cursor):
            return client.comments.list(block_id=block_id, start_cursor=start_cursor)

        def retrieve_page(self, page_id):
            return client.pages.retrieve(page_id=page_id)

    return _RealTransport()
