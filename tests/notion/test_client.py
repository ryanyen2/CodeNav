"""U7 — NotionClient against a fake transport (no network, no token)."""
from __future__ import annotations

import pytest

from codoc.notion.client import (
    NotionAPIError, NotionClient, NotionConcurrencyError, _RateLimiter,
)
from codoc.notion.config import NotionConfig

_CFG = NotionConfig(token="t", page_id="page-root")


class FakeTransport:
    """Records calls; returns scripted responses. Each method can be driven per-test."""

    def __init__(self):
        self.children: dict[str, list[dict]] = {}
        self.children_pages: dict[str, list[dict]] = {}  # block_id -> list of page responses
        self.comments_pages: list[dict] = []
        self.page_meta: dict = {}
        self.update_calls: list[list[dict]] = []
        self.update_error: NotionAPIError | None = None
        self.list_calls = 0
        self._fail_times = 0

    def fail_next(self, n: int, status: int, retry_after: float = 0):
        self._fail_times = n
        self._fail_status = status
        self._fail_retry = retry_after

    def list_block_children(self, block_id, start_cursor):
        self.list_calls += 1
        if self._fail_times:
            self._fail_times -= 1
            raise NotionAPIError(self._fail_status, "rate_limited", retry_after=self._fail_retry)
        if block_id in self.children_pages:
            pages = self.children_pages[block_id]
            idx = 0 if start_cursor is None else int(start_cursor)
            return pages[idx]
        return {"results": self.children.get(block_id, []), "has_more": False, "next_cursor": None}

    def update_page_markdown(self, page_id, commands):
        self.update_calls.append(commands)
        if self.update_error:
            raise self.update_error
        return {"ok": True}

    def append_block_children(self, parent_id, children):
        return {"ok": True}

    def delete_block(self, block_id):
        return {"ok": True}

    def list_comments(self, block_id, start_cursor):
        idx = 0 if start_cursor is None else int(start_cursor)
        return self.comments_pages[idx]

    def retrieve_page(self, page_id):
        return self.page_meta


def _client(transport, **kw):
    # no-op sleep/clock so tests never actually wait
    clock = {"t": 0.0}
    return NotionClient(_CFG, transport, sleep=lambda s: None,
                        clock=lambda: clock["t"], **kw)


# ── rate limiter ─────────────────────────────────────────────────────────────

def test_rate_limiter_sleeps_when_empty():
    slept = []
    t = {"now": 0.0}
    rl = _RateLimiter(rate=3.0, capacity=3.0, sleep=lambda s: slept.append(s),
                      clock=lambda: t["now"])
    for _ in range(3):  # drain the burst
        rl.acquire()
    rl.acquire()  # 4th in the same instant → must sleep
    assert slept and slept[0] > 0


# ── backoff on 429 ───────────────────────────────────────────────────────────

def test_retries_on_429_then_succeeds():
    t = FakeTransport()
    t.children["page-root"] = [{"id": "b1", "type": "toggle"}]
    t.fail_next(2, status=429, retry_after=0)
    client = _client(t)
    tree = client.get_block_tree()
    assert tree[0]["id"] == "b1"
    assert t.list_calls == 3  # 2 failures + 1 success


def test_gives_up_after_max_retries():
    t = FakeTransport()
    t.fail_next(99, status=429, retry_after=0)
    client = _client(t, max_retries=2)
    with pytest.raises(NotionAPIError):
        client.get_block_tree()


# ── pagination + recursive hydration ─────────────────────────────────────────

def test_get_block_tree_paginates():
    t = FakeTransport()
    t.children_pages["page-root"] = [
        {"results": [{"id": "a", "type": "toggle"}], "has_more": True, "next_cursor": "1"},
        {"results": [{"id": "b", "type": "toggle"}], "has_more": False, "next_cursor": None},
    ]
    tree = _client(t).get_block_tree()
    assert [b["id"] for b in tree] == ["a", "b"]


def test_get_block_tree_hydrates_children():
    t = FakeTransport()
    t.children["page-root"] = [{"id": "parent", "type": "toggle", "has_children": True}]
    t.children["parent"] = [{"id": "kid", "type": "paragraph"}]
    tree = _client(t).get_block_tree()
    assert tree[0]["children"][0]["id"] == "kid"


# ── anchored write concurrency ───────────────────────────────────────────────

def test_update_content_sends_commands():
    t = FakeTransport()
    _client(t).update_content([("old text", "new text")])
    assert t.update_calls == [[{"old_str": "old text", "new_str": "new text"}]]


def test_update_content_anchor_miss_raises_concurrency():
    t = FakeTransport()
    t.update_error = NotionAPIError(400, "validation_error", "old_str not found")
    with pytest.raises(NotionConcurrencyError):
        _client(t).update_content([("missing anchor", "x")])


def test_update_content_other_error_propagates():
    t = FakeTransport()
    t.update_error = NotionAPIError(403, "unauthorized", "no access")
    with pytest.raises(NotionAPIError):
        _client(t).update_content([("a", "b")])


# ── comments + last_edited_time ──────────────────────────────────────────────

def test_list_comments_paginates():
    t = FakeTransport()
    t.comments_pages = [
        {"results": [{"id": "c1"}], "has_more": True, "next_cursor": "1"},
        {"results": [{"id": "c2"}], "has_more": False, "next_cursor": None},
    ]
    assert [c["id"] for c in _client(t).list_comments("blk")] == ["c1", "c2"]


def test_last_edited_time():
    t = FakeTransport()
    t.page_meta = {"last_edited_time": "2026-06-26T00:00:00.000Z"}
    assert _client(t).last_edited_time() == "2026-06-26T00:00:00.000Z"
