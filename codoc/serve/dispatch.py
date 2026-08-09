"""dispatch.py — capability-gated routing of browser commands to file channels (U5).

Every ``WebviewMessage`` (protocol.ts) a remote browser posts is routed here. Two
invariants enforce "outsiders can only suggest":

  • Capability gating — a SUGGEST role (read collaborator) may settle/comment/
    withdraw-own; only a HANDOFF role (write collaborator) may write verdicts or
    hand off. NONE is denied everything.
  • Safe-by-default settle — a remote ``commit`` (the editor's "Save" gesture) is
    treated as a HELD settle, NOT auto-sent. The ONLY suggestion→execution crossing is
    the explicit ``hand-off`` command, which the realization trigger (U7) consumes. So
    even though the daemon may write realize.md, nothing runs until an authorized
    hand-off — the trigger is the gate, which is where that decision belongs.

The EDIT itself does not arrive here as a document. A settle is an acknowledgement; the
author's change arrives as identity-keyed ``commands`` (the five kinds below), emitted by
the browser through the same modules the VS Code host uses
(``vscode-codoc/src/webview/command-emitter.ts``). This handler used to write the posted
doc to ``tree.doc.json``, which stopped being an input at U4 (the daemon became its sole
writer) and stopped being read as one at U7 (the doc-diff inference was retired): the
remote author's prose was overwritten by the next daemon render, and the write itself made
``reconcile.safe_write_tree`` treat the projection as ahead of the store and skip
re-rendering both exports. Nothing here writes a derived artifact any more.

Transport-agnostic: ``dispatch`` takes a parsed message + the caller's capability;
the HTTP/CSRF/session wiring lives in app.py. All file writes go through the
locked edits.json / inbox.json mutators (U5 lock); ``tree.codoc`` is never written.
"""
from __future__ import annotations

import time
from pathlib import Path

from codoc.serve.auth import Capability



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

def _noop(_message: dict, _codoc_dir: str) -> dict:
    return {"ok": True}


def _settle(_message: dict, _codoc_dir: str) -> dict:
    """Acknowledge a settle / commit. It carries no content to store.

    The edit arrives as identity-keyed commands (see the module docstring), each carrying
    the ``base_text`` its author last knew — so concurrency is resolved per FIELD by the
    daemon's three-way merge (``loop_b._resolve_content``), which is what U9's whole-doc
    ``baseRev`` guard existed to approximate until the finer merge landed. That guard is
    gone with the doc write it protected: rejecting a contentless ack with 409 would tell
    the client its change was refused (a 4xx is dropped from the outbox and surfaced to
    the author) when nothing about the change was even in the message.

    ``held`` stays in the reply: a remote commit records the edit and waits for an
    explicit hand-off, and the client reads that as confirmation."""
    return {"ok": True, "held": True}


def _verdict(message: dict, codoc_dir: str) -> dict:
    from codoc.loop import inbox

    accept = bool(message.get("accept"))
    ids = [e for e in (message.get("eventIds") or []) if isinstance(e, str) and e]
    for eid in ids:
        inbox.append_verdict(codoc_dir, eid, accept=accept)
    return {"ok": True, "verdicts": len(ids)}


def _hand_off(_message: dict, codoc_dir: str) -> dict:
    """Release every held directive to the agent — the one suggestion→execution crossing.

    ``handoffs`` is the POSITIVE signal: Loop B flips a held directive to handed_off when
    its feature appears there, and the U7 trigger runs from realize.md. Clearing ``drafts``
    is only the UI half ("captured" drops); on its own it hands nothing off, which is what
    this did after the held-draft model landed — a maintainer's hand-off on the hub
    silently did nothing. The IDE writes both (tree-editor.handOff) and so does this."""
    from codoc.loop import edits

    held = list(dict.fromkeys(
        d.feature_id for d in edits.read_manifest(codoc_dir)
        if d.feature_id and not d.handed_off))
    if held:
        edits.append_handoffs(codoc_dir, held)
    edits.set_drafts(codoc_dir, [])
    return {"ok": True, "handed_off": len(held)}


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
    return {"ok": True}


def _command(message: dict, codoc_dir: str) -> dict:
    """Persist an identity-keyed authored command (U3) to edits.json. The
    capability gate already ran in :func:`dispatch` (set_title/set_description need
    only SUGGEST; add/move/retire need HANDOFF — KTD10), so by the time we get here
    the kind is allowed for this caller. The daemon's Loop B drains + applies it via
    ``apply_op`` (idempotent on the command id). The wire shape mirrors the Python
    ``Command`` dataclass: ``{kind, id, featureId?, localId?, baseText?, session?,
    payload?}``. An older client's ``baseRev`` is ignored: the per-feature integer gate it
    fed was superseded by ``base_text`` (the value the author last knew), which is what the
    daemon merges from."""
    from codoc.loop import edits
    from codoc.loop.edits import Command

    kind = message.get("kind") or ""
    cid = message.get("id") or message.get("commandId") or ""
    if not cid:
        raise CommandError(f"'{kind}' command requires an id (idempotency key)")
    payload = message.get("payload")
    base_text = message.get("baseText")
    if not isinstance(base_text, str):
        base_text = message.get("base_text")
    edits.append_command(codoc_dir, Command(
        id=cid, kind=kind,
        feature_id=message.get("featureId") or message.get("feature_id") or "",
        local_id=message.get("localId") or message.get("local_id") or "",
        base_text=base_text if isinstance(base_text, str) else None,
        session=message.get("session") or "",
        payload=dict(payload) if isinstance(payload, dict) else {}))
    return {"ok": True, "queued": True}


def _comment_passthrough(_message: dict, _codoc_dir: str) -> dict:
    """comment-edit / comment-resolve: acknowledged, not yet applied remotely.

    These used to be implemented by writing the posted doc to ``tree.doc.json``, which
    carried the mark edit/removal — a shared-writer arrangement that U4 ended (comment
    threads live in the store's ``comments`` table and the daemon owns the projection).
    There is no remote channel for editing or resolving an existing thread yet:
    ``edits.json`` carries one-shot ``steers``, which is what ``comment-create`` uses.
    Acknowledging is honest; the write it replaced did not apply the change either, and
    it made the daemon skip re-rendering the exports as a side effect."""
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
