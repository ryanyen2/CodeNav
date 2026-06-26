"""bridge.py — the Notion bridge process.

Wires the pieces into a running host: it DEFERS to an existing daemon owner (never
double-spawns ``codoc watch`` — the single-owner model), maintains the
``block_id ↔ feature_id`` identity map, and runs two directions:

* **inbound** — a webhook/poll trigger → fetch the page's block tree → dispatch
  edits + verdicts into the ``.codoc`` channels (the daemon's Loop B applies them);
* **outbound** — a ``.codoc/*`` change → re-render the tree → dedup'd push back to
  Notion (the echo-loop guard keeps this from re-triggering inbound).

The pure cycle (``reconcile_inbound`` / ``push_outbound``), the identity map, and
the ownership check are unit-tested against a fake client; the async webhook+poll
loop (``run_bridge``) is thin wiring validated against a live workspace.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from codoc.loop import watch
from codoc.loop.fsio import atomic_write_json, read_json
from codoc.notion.config import NotionConfig
from codoc.notion.dispatch import DispatchResult, dispatch_notion_edits, handle_comment_verdict
from codoc.notion.push import BlockPushStream
from codoc.notion.render import recover_event_id, render_blocks
from codoc.store.db import Store, open_store

_MAP_FILENAME = "notion_map.json"


class BridgeOwnershipError(RuntimeError):
    """No daemon owns the repo — the bridge defers rather than spawning its own."""


def ensure_daemon_owner(codoc_dir: str | Path) -> None:
    """Raise unless a live ``codoc watch`` daemon owns the repo. The bridge is a
    file-channel client; it needs the daemon to drain the channels it writes."""
    if not watch.daemon_running(str(codoc_dir)):
        raise BridgeOwnershipError(
            "no codoc daemon owns this repo — start `codoc watch` (or `codoc serve`) "
            "first; the Notion bridge defers to it and never spawns its own."
        )


class NotionMap:
    """Persistent ``feature_id → notion_block_id`` map (``notion_map.json``), owned by
    the bridge. The inverse keys the parser; the forward map stamps render ids."""

    def __init__(self, codoc_dir: str | Path):
        self._path = Path(codoc_dir) / _MAP_FILENAME
        data = read_json(self._path, default={}) or {}
        self._fid_to_block: dict[str, str] = dict(data.get("fid_to_block") or {})

    def fid_to_block(self) -> dict[str, str]:
        return dict(self._fid_to_block)

    def block_to_fid(self) -> dict[str, str]:
        return {b: f for f, b in self._fid_to_block.items()}

    def set(self, feature_id: str, block_id: str) -> None:
        if feature_id and block_id:
            self._fid_to_block[feature_id] = block_id

    def learn_from_store(self, store: Store, block_ids: set[str]) -> int:
        """Map any feature whose ``local_id`` is one of the Notion block ids we saw —
        i.e. a feature minted from a Notion ADD (its ADD carried local_id=block_id).
        Returns how many new mappings were learned."""
        learned = 0
        for f in store.list_features():
            if f.local_id and f.local_id in block_ids and self._fid_to_block.get(f.id) != f.local_id:
                self._fid_to_block[f.id] = f.local_id
                learned += 1
        return learned

    def save(self) -> None:
        atomic_write_json(self._path, {"version": 1, "fid_to_block": self._fid_to_block})


def iter_blocks(blocks: list[dict]) -> Iterator[dict]:
    """Depth-first walk over a hydrated block tree (parents before children)."""
    for b in blocks or []:
        if isinstance(b, dict):
            yield b
            yield from iter_blocks(b.get("children") or [])


def collect_verdicts(client, blocks: list[dict]) -> list[tuple[str, str, str]]:
    """For each proposal callout in the tree, read its comments and pull verdict
    commands. Returns ``(block_id, event_id, comment_text)`` for callouts that have
    comments — the caller applies them. (Listing comments per callout, not per page,
    keeps the comment→proposal binding unambiguous.)"""
    found: list[tuple[str, str, str]] = []
    for block in iter_blocks(blocks):
        if block.get("type") != "callout":
            continue
        event_id = recover_event_id(block)
        if not event_id:
            continue
        for comment in client.list_comments(block["id"]):
            text = _comment_text(comment)
            if text:
                found.append((block["id"], event_id, text))
    return found


def _comment_text(comment: dict) -> str:
    """Project a Notion comment's rich_text to plain text."""
    from codoc.notion.parse import rich_text_to_markdown

    return rich_text_to_markdown(comment.get("rich_text")) if isinstance(comment, dict) else ""


def reconcile_inbound(codoc_dir: str | Path, store: Store, client,
                      notion_map: NotionMap) -> DispatchResult:
    """One inbound cycle: fetch the page, dispatch edits + verdicts to the channels,
    and learn identity for any feature the dispatch will mint. Idempotent — an
    unchanged page writes nothing."""
    blocks = client.get_block_tree()
    block_ids = {b["id"] for b in iter_blocks(blocks) if b.get("id")}

    result = dispatch_notion_edits(codoc_dir, store, blocks, notion_map.block_to_fid())

    # Apply verdict commands from proposal-callout comments.
    for _block_id, event_id, text in collect_verdicts(client, blocks):
        if handle_comment_verdict(codoc_dir, event_id, text) is not None:
            result.steers += 0  # verdicts tracked separately; count kept simple here

    # Learn identity for features whose local_id is a Notion block id (Notion-minted).
    if notion_map.learn_from_store(store, block_ids):
        notion_map.save()
    return result


def push_outbound(codoc_dir: str | Path, store: Store, client,
                  notion_map: NotionMap, stream: BlockPushStream) -> bool:
    """One outbound cycle: re-render the tree and, only if it changed since the last
    push (echo-loop guard), write it to Notion. Returns whether a push happened.

    Block-level write reconciliation against live Notion (create vs update vs delete)
    is validated against a real workspace; here we render with current ids and hand
    the tree to the client's write path."""
    blocks = stream.next_if_changed()
    if blocks is None:
        return False
    client.write_page_tree(notion_map.fid_to_block(), blocks)
    return True


def run_bridge(config: NotionConfig, codoc_dir: str | Path, *,
               host: str = "127.0.0.1", port: int = 8788,
               printer=print) -> None:  # pragma: no cover - live process loop
    """Process entrypoint: defer to the daemon owner, then run the inbound/outbound
    loop (webhook when configured, else polling). Lazy-builds the real client."""
    import time

    from codoc.notion.client import NotionClient, build_real_transport
    from codoc.notion.webhook import PollState

    ensure_daemon_owner(codoc_dir)
    client = NotionClient(config, build_real_transport(config))
    notion_map = NotionMap(codoc_dir)
    stream = BlockPushStream(lambda: render_blocks(open_store(codoc_dir),
                                                   fid_to_block=notion_map.fid_to_block()))

    mode = "webhook+polling" if config.webhooks_enabled else "polling"
    printer(f"codoc notion · {mode} · deferring to daemon · {codoc_dir}")

    poll = PollState()
    while True:
        with open_store(codoc_dir) as store:
            if poll.advanced(client.last_edited_time()):
                reconcile_inbound(codoc_dir, store, client, notion_map)
            push_outbound(codoc_dir, store, client, notion_map, stream)
        time.sleep(config.poll_interval_seconds)
