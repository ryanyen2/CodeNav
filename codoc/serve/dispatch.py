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
# a HELD settle (see module docstring) — it does not auto-send.
_SUGGEST_KINDS = frozenset({
    "ready", "doc-settle", "commit",
    "comment-create", "comment-edit", "comment-resolve",
    "withdraw-realization", "set-pref",
})
# Kinds that require a HANDOFF (write) role — the suggestion→execution crossing.
_HANDOFF_KINDS = frozenset({"verdict", "hand-off"})


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


def _comment_passthrough(message: dict, codoc_dir: str) -> dict:
    # comment-edit / comment-resolve: persist the doc (mark edit/removal); the
    # `> …` steering lifecycle is reconciled by the daemon.
    _persist_doc(message, codoc_dir)
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
    "set-pref": _noop,
}
