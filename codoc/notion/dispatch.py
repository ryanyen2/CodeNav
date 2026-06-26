"""dispatch.py — route Notion-page edits into the ``.codoc`` channels.

The Notion analogue of ``serve/dispatch.py``: it never mutates the store and never
writes ``tree.codoc``. It diffs the (hydrated) Notion block tree against the store
and writes the result to two append-only, filelock-guarded channels that Loop B
drains:

* structural + amend ops (ADD / AMEND / MOVE / RETIRE) → the ``node_ops`` channel,
  applied and **auto-handed-off** by Loop B (no draft id → realized immediately —
  the "authoritative authoring" posture the Notion host chose);
* steering quotes on live features → the ``steers`` channel (one-shot STEER
  directives), with an id-scoped ``comment_id`` so two byte-identical notes don't
  collapse.

Idempotency falls out of ``diff_codoc``: an unchanged page diffs to nothing, so a
re-sync writes nothing — the same property the echo-loop guard relies on.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import re

from codoc.codoc_file.diff import diff_codoc
from codoc.loop import edits as edits_channel
from codoc.loop import inbox
from codoc.loop.edits import Steer
from codoc.notion.parse import parse_blocks
from codoc.store.db import Store

# A verdict command typed in a Notion comment on a proposal callout. Leading slash,
# case-insensitive, optionally surrounded by other words ("looks good /accept").
_ACCEPT_RE = re.compile(r"(?:^|\s)/accept\b", re.IGNORECASE)
_REJECT_RE = re.compile(r"(?:^|\s)/reject\b", re.IGNORECASE)


@dataclass
class DispatchResult:
    """What a single dispatch wrote — for logging and tests."""
    node_ops: int = 0
    steers: int = 0
    handoffs: int = 0


def _steer_comment_id(feature_id: str, text: str) -> str:
    """A deterministic, id-scoped thread key for a steering quote, so two identical
    notes are distinct threads (avoids the ``(feature_id, text)`` collapse residual).
    Stable across re-syncs of the same quote so it isn't re-queued endlessly."""
    import hashlib

    digest = hashlib.sha1(f"{feature_id}\x00{text}".encode()).hexdigest()[:12]
    return f"notion:{feature_id}:{digest}"


def dispatch_notion_edits(
    codoc_dir: str | Path,
    store: Store,
    blocks: list[dict],
    block_to_fid: dict[str, str] | None = None,
) -> DispatchResult:
    """Diff the Notion block tree against the store and write the derived edits to
    the channels. Returns a summary; writes nothing when the page is unchanged."""
    parsed = parse_blocks(blocks, block_to_fid)
    diff = diff_codoc(parsed, store, has_local_ids=True)

    result = DispatchResult()
    if diff.user_ops:
        edits_channel.append_node_ops(codoc_dir, diff.user_ops)
        result.node_ops = len(diff.user_ops)
        # Authoritative auto-handoff: an AMEND/MOVE directive is "born held" and
        # flips to handed-off only when its feature appears in the handoffs channel.
        # The Notion host's edits are authoritative, so hand off every edited feature
        # (RETIRE/plan-ADD already hand off on mint; ADDs have no id yet — descriptive
        # adds mint no directive anyway).
        handoff_fids = sorted({op.feature_id for op in diff.user_ops if op.feature_id})
        if handoff_fids:
            edits_channel.append_handoffs(codoc_dir, handoff_fids)
            result.handoffs = len(handoff_fids)

    # Steering quotes on existing features (new-node comments need the minted id and
    # are deferred — adding a node and steering it in the same sync is rare).
    for feature_id, text in diff.comments:
        edits_channel.append_steer(
            codoc_dir,
            Steer(feature_id=feature_id, text=text,
                  comment_id=_steer_comment_id(feature_id, text)),
        )
        result.steers += 1

    return result


def parse_verdict_command(text: str) -> bool | None:
    """``/accept`` → True, ``/reject`` → False, neither → None. If both appear, the
    first one in the text wins (an explicit later correction beats an earlier word)."""
    if not text:
        return None
    accept = _ACCEPT_RE.search(text)
    reject = _REJECT_RE.search(text)
    if accept and reject:
        return accept.start() < reject.start()
    if accept:
        return True
    if reject:
        return False
    return None


def submit_verdict(codoc_dir: str | Path, event_id: str, accept: bool):
    """Write an accept/reject verdict to the inbox channel (Loop B applies it).

    The verdict is the authority — never also mutate the Notion page to reflect it
    (that would be a local double-apply); Loop B applies the proposal and the next
    push reflects the result."""
    return inbox.append_verdict(codoc_dir, event_id, accept)


def handle_comment_verdict(codoc_dir: str | Path, event_id: str, comment_text: str) -> bool | None:
    """Parse a proposal-callout comment and, when it carries a verdict command, write
    it to the inbox. Returns the verdict (True/False) when one was written, else None."""
    verdict = parse_verdict_command(comment_text)
    if verdict is None or not event_id:
        return None
    submit_verdict(codoc_dir, event_id, verdict)
    return verdict
