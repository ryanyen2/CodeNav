"""dispatch.py — capability-gated routing of browser commands to file channels (U5).

Every ``WebviewMessage`` (protocol.ts) a remote browser posts is routed here. Two
invariants enforce "outsiders can only suggest":

  • Capability gating — a SUGGEST role (read collaborator) may settle/comment/
    withdraw-own; only a HANDOFF role (write collaborator) may write verdicts or
    hand off. NONE is denied everything.
  • Safe-by-default settle — a remote ``commit`` (the editor's "Save" gesture) is
    treated as a HELD settle, NOT auto-sent: it persists the doc but does not cross
    to execution. The ONLY suggestion→execution crossing is the explicit
    ``hand-off`` command, which the realization trigger (U7) consumes. So even
    though the daemon may write realize.md, nothing runs until an authorized
    hand-off — the trigger is the gate, which is where that decision belongs.

Transport-agnostic: ``dispatch`` takes a parsed message + the caller's capability;
the HTTP/CSRF/session wiring lives in app.py. All file writes go through the
locked edits.json / inbox.json mutators (U5 lock); ``tree.codoc`` is never written.
"""
from __future__ import annotations

import time
from pathlib import Path

from codoc.serve.auth import Capability

_DOC_FILENAME = "tree.doc.json"


class CommandError(Exception):
    """A rejected command. ``status`` maps to the HTTP status the route returns."""

    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.status = status


# Command kinds a SUGGEST (read) role may issue. `commit` is included but routes to
# a HELD settle (see module docstring) — it does not auto-send. The identity-keyed
# command kinds (U3 / KTD10): a description-level edit (`set_title`/`set_description`)
# is SUGGEST-eligible — an outsider may propose prose; the structural kinds
# (`add`/`move`/`retire`) are HANDOFF-gated below. `block-edit` joins here for the
# same reason as `set_title`/`set_description`: a typed-media block edit is content,
# not structure, and Loop B's `lower` dispatch already routes an ambiguous/lossy
# result to the held-draft gate — a remote suggester can propose one, never force
# an immediate code change.
_SUGGEST_KINDS = frozenset({
    "ready", "doc-settle", "commit",
    "comment-create", "comment-edit", "comment-resolve",
    "withdraw-realization", "set-pref",
    "set_title", "set_description", "block-edit",
})
# Kinds that require a HANDOFF (write) role — the suggestion→execution crossing. The
# structural identity-keyed commands (U3 / KTD10) join here: only a write collaborator
# may add/move/retire a feature directly; a suggest-only client's structural change is
# queued as a pending proposal, never applied.
_HANDOFF_KINDS = frozenset({"verdict", "hand-off", "add", "move", "retire"})


def allowed(kind: str | None, capability: Capability) -> bool:
    if capability is Capability.HANDOFF:
        return kind in _SUGGEST_KINDS or kind in _HANDOFF_KINDS
    if capability is Capability.SUGGEST:
        return kind in _SUGGEST_KINDS
    return False


def dispatch(message: dict, capability: Capability, codoc_dir: str | Path) -> dict:
    """Route one command. Raises :class:`CommandError` on a capability violation,
    an unknown kind, or a malformed payload."""
    kind = message.get("kind") if isinstance(message, dict) else None
    if not kind:
        raise CommandError("missing command kind")
    if not allowed(kind, capability):
        raise CommandError(f"{capability.value} role may not '{kind}'", status=403)
    handler = _HANDLERS.get(kind)
    if handler is None:
        raise CommandError(f"unsupported command '{kind}'")
    return handler(message, str(codoc_dir))


# ── handlers ───────────────────────────────────────────────────────────────

def _persist_doc(message: dict, codoc_dir: str) -> None:
    """Persist the browser's whole-doc edit to tree.doc.json (the authoritative
    webview artifact the daemon's Loop B reads). Never writes tree.codoc."""
    doc = message.get("doc")
    if doc is None:
        return
    from codoc.loop.fsio import atomic_write_json

    atomic_write_json(Path(codoc_dir) / _DOC_FILENAME, doc)


def _noop(_message: dict, _codoc_dir: str) -> dict:
    return {"ok": True}


def _settle(message: dict, codoc_dir: str) -> dict:
    # Optimistic concurrency (U9): if the client tells us which version it edited
    # from (`baseRev`) and the hub has since advanced past it, another writer's
    # change would be clobbered — reject so the browser reloads the fresh snapshot.
    # A whole-doc last-write-wins guarded by the store-derived version; finer
    # per-feature CRDT merge is the deferred Tier-2 work. Absent baseRev → no guard.
    base_rev = message.get("baseRev")
    if isinstance(base_rev, int):
        from codoc.serve.payload import payload_version

        if base_rev < payload_version(codoc_dir):
            raise CommandError("stale doc — reload and retry", status=409)
    # Held settle: persist the doc; execution waits for an explicit hand-off (U7).
    _persist_doc(message, codoc_dir)
    return {"ok": True, "held": True}


def _verdict(message: dict, codoc_dir: str) -> dict:
    from codoc.loop import inbox

    accept = bool(message.get("accept"))
    ids = [e for e in (message.get("eventIds") or []) if isinstance(e, str) and e]
    for eid in ids:
        inbox.append_verdict(codoc_dir, eid, accept=accept)
    return {"ok": True, "verdicts": len(ids)}


def _hand_off(_message: dict, codoc_dir: str) -> dict:
    from codoc.loop import edits

    # Clear the held drafts — the daemon's next pass marks every held directive
    # handed_off; the U7 realization trigger then runs the frozen snapshot.
    edits.set_drafts(codoc_dir, [])
    return {"ok": True}


def _withdraw(message: dict, codoc_dir: str) -> dict:
    from codoc.loop import edits

    fid = message.get("featureId")
    if not isinstance(fid, str) or not fid:
        raise CommandError("withdraw-realization requires featureId")
    edits.append_cancellation(codoc_dir, fid)
    return {"ok": True}


def _comment_create(message: dict, codoc_dir: str) -> dict:
    from codoc.loop import edits
    from codoc.loop.edits import Steer

    thread = message.get("thread") or {}
    fid = thread.get("featureId") or thread.get("feature_id")
    text = thread.get("body") or thread.get("text") or thread.get("note") or ""
    cid = thread.get("id") or ""
    if not fid or not text:
        raise CommandError("comment-create requires thread featureId + body")
    edits.append_steer(codoc_dir, Steer(feature_id=fid, text=text, comment_id=cid,
                                        ts=int(time.time() * 1000)))
    _persist_doc(message, codoc_dir)
    return {"ok": True}


def _command(message: dict, codoc_dir: str) -> dict:
    """Persist an identity-keyed authored command (U3) to edits.json. The
    capability gate already ran in :func:`dispatch` (set_title/set_description need
    only SUGGEST; add/move/retire need HANDOFF — KTD10), so by the time we get here
    the kind is allowed for this caller. The daemon's Loop B drains + applies it via
    ``apply_op`` (idempotent on the command id). The wire shape mirrors the Python
    ``Command`` dataclass: ``{kind, id, featureId?, localId?, baseRev?, payload?}``."""
    from codoc.loop import edits
    from codoc.loop.edits import Command

    kind = message.get("kind") or ""
    cid = message.get("id") or message.get("commandId") or ""
    if not cid:
        raise CommandError(f"'{kind}' command requires an id (idempotency key)")
    payload = message.get("payload")
    edits.append_command(codoc_dir, Command(
        id=cid, kind=kind,
        feature_id=message.get("featureId") or message.get("feature_id") or "",
        local_id=message.get("localId") or message.get("local_id") or "",
        base_rev=int(message.get("baseRev") or message.get("base_rev") or 0),
        payload=dict(payload) if isinstance(payload, dict) else {}))
    return {"ok": True, "queued": True}


def _comment_passthrough(message: dict, codoc_dir: str) -> dict:
    # comment-edit / comment-resolve: persist the doc (mark edit/removal); the
    # `> …` steering lifecycle is reconciled by the daemon.
    _persist_doc(message, codoc_dir)
    return {"ok": True}


def _block_edit(message: dict, codoc_dir: str) -> dict:
    """Persist a typed-media block edit (v6) to the `block_edits` channel — the
    SAME persistence the local webview's `handleBlockEdit` uses (KTD5/KTD8), so a
    remote suggestion and a local edit are indistinguishable once written. Loop B
    drains it and dispatches the block's declared `lower` capability; an
    add/edit's *content* is written to the store immediately (it is the visible
    projection, same as a doc-ahead suggestion), while any resulting code-implying
    directive still inherits the draft/hand-off gate — a remote suggester cannot
    force realization, only propose it."""
    from codoc.loop import edits
    from codoc.loop.edits import BlockEdit

    block = message.get("block") or {}
    block_id = block.get("block_id") or ""
    feature_id = block.get("feature_id") or ""
    kind = block.get("kind") or ""
    if not block_id or not feature_id or not kind:
        raise CommandError("block-edit requires block_id, feature_id, kind")
    content = block.get("content") or ""
    media_data = block.get("mediaData") or ""
    if media_data:
        from codoc.serve.media import save_media_attachment

        ref = save_media_attachment(codoc_dir, block_id, media_data, block.get("mediaMime") or "")
        if ref:
            content = ref
    edits.append_block_edit(codoc_dir, BlockEdit(
        block_id=block_id, feature_id=feature_id, kind=kind,
        action=block.get("action") or "edit",
        content=content,
        prev_content=block.get("prev_content") or "",
        ts=int(time.time() * 1000)))
    return {"ok": True}


_HANDLERS = {
    "ready": _noop,
    "doc-settle": _settle,
    "commit": _settle,
    "verdict": _verdict,
    "hand-off": _hand_off,
    "withdraw-realization": _withdraw,
    "comment-create": _comment_create,
    "comment-edit": _comment_passthrough,
    "comment-resolve": _comment_passthrough,
    "block-edit": _block_edit,
    "set-pref": _noop,
    # Identity-keyed authored commands (U3): all five route to the same persist
    # handler; the per-kind capability gate runs in dispatch() (KTD10).
    "add": _command,
    "set_title": _command,
    "set_description": _command,
    "move": _command,
    "retire": _command,
}
