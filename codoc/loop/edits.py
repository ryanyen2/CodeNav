"""``.codoc/edits.json`` + ``.codoc/realize.json`` — the provenance/intent channel.

The frontend's rich doc knows WHO is editing (human or agent, pen or suggest);
the loops only see ``tree.codoc`` text. These two small files bridge the gap
without making Python read ``tree.doc.json``:

* ``edits.json`` (host-written, modeled on ``inbox.json``)::

      {"version": 1,
       "edits":   [{"feature_id": "f-…", "fields": ["description"],
                    "actor": "human", "mode": "pen",
                    "suggestion_id": "", "ts": 0}],
       "intents": [{"id": "d-f123", "feature_id": "f-…",
                    "actor": "human", "ts": 0}],
       "cancellations": [{"feature_id": "f-…", "ts": 0}]}

  - ``edits`` are per-feature authorship annotations for settles: Loop B drains
    them (``drain_annotations``) and stamps the matching user ops' events with
    actor/mode (default human/pen when absent). ``suggestion_id`` links a settle
    that applied a doc-ahead suggestion, so the queued directive can carry it as
    ``caused_by``. A stale annotation can at worst mislabel actor/mode (display
    provenance) — it never affects what is applied.
  - ``intents`` are the LIVE doc-ahead suggestions — the doc-wins hold set
    (classify table row 13/9). The host adds an intent on suggest-create and
    removes it on withdraw / once satisfied; the loops never WRITE the list.
    An intent carrying a payload (suggested ``title``/``description``) is
    *applied* by Loop B — the agent-side "apply" (the human's only verb on
    their own suggestion is Withdraw): the loop applies it as a user op
    (mode=suggest, caused_by=suggestion id) and, when imperative, queues a
    realize directive. Intents whose payload matches the store are satisfied
    and skipped, so the read-only drain is idempotent.
  - ``cancellations`` are realize-WITHDRAWALS (U6): feature ids whose queued
    directive the human asked to cancel. Loop B drains them and prunes the
    matching directive from ``realize.json`` (releasing the doc-wins hold) and
    rebuilds/removes ``realize.md``. The committed prose is KEPT — withdraw
    cancels the code realization, not the documented intent (re-wording it is a
    normal edit).
  - ``steers`` are one-shot inline-comment notes (U2b): once the host stopped
    writing ``tree.codoc`` (single-writer), an inline ``> …`` comment can no
    longer ride the text round-trip, so the webview hands it here; Loop B drains
    each into a STEER directive exactly once (same one-shot pattern as ``edits``).

* ``realize.json`` (Loop-B-written next to ``realize.md``)::

      {"version": 1, "directives": [{"id": "d-…", "feature_id": "f-…",
                                     "kind": "amend", "caused_by": "…"}]}

  The machine-readable manifest of the queued directives: ids for the causality
  chain (``/codoc:sync`` passes the ``⟨d-id⟩`` it implements to
  ``codoc_reflect(caused_by=…)``; epoch-close Loop A tags its ops likewise) and
  feature ids for the hold set. Deleted together with ``realize.md`` when the
  queue completes; a manifest with no ``realize.md`` beside it is stale and is
  ignored (and cleaned up opportunistically).

* ``edits.host.jsonl`` (IDE-written APPEND-ONLY op log; U9) — the VS Code host is a
  separate process that does NOT hold this module's cross-process lock, so it must not
  read-modify-write ``edits.json`` (a lock-less RMW could clobber the daemon's/hub's
  locked RMW — a lost command / hand-off / steer — and its fixed-tmp rename could ENOENT-
  crash). Instead it APPENDS one op per line (``{"fn", "arg"}``, one of the ``append_*`` /
  ``set_drafts`` writers), and :func:`merge_host_ops` folds the log into ``edits.json``
  under the lock at the start of every Loop B pass. O_APPEND is atomic per small write, so
  two windows can append concurrently; pure append means the host never re-includes an
  already-merged op.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from codoc.loop.filenames import (
    DRIFT_FILENAME,
    EDITS_FILENAME,
    HOST_OPS_FILENAME,
    REALIZE_FILENAME,
    REALIZE_MANIFEST_FILENAME,
    REALIZED_LOG_FILENAME,
    RESOLUTION_FILENAME,
)
from codoc.loop.fsio import atomic_write_json, read_json

# Intents older than this are ignored by the hold set (an abandoned suggestion
# must not hold a feature forever). The host clears satisfied intents itself;
# this is only the backstop. Timestamps are unix epoch MILLISECONDS (Date.now()).
INTENT_STALE_MS = 7 * 24 * 3600 * 1000

# edits.json schema version. Bumped to 2 for the identity-keyed ``commands`` channel
# (U3): authored edits arrive as explicit commands applied via ``apply_op``, no longer
# inferred from a doc diff. Readers don't gate on this — a v1 file (no ``commands`` key)
# reads as empty (the ``_LISTS`` merge in :func:`_rewrite` already gives that) — so the
# bump is a signal, not a barrier. The TS mirror (edits-channel.ts) pins the same value.
EDITS_VERSION = 2


@dataclass
class EditAnnotation:
    feature_id: str
    fields: list[str] = field(default_factory=list)   # ["title"], ["description"], …
    actor: str = "human"
    mode: str = "pen"
    suggestion_id: str = ""  # set when this settle applied a doc-ahead suggestion
    ts: int = 0              # unix ms


@dataclass
class Steer:
    """A one-shot inline-comment steer (U2b): the webview hands an inline `> …`
    comment to Loop B through edits.json instead of the tree.codoc text round-trip
    (the host no longer writes tree.codoc). Drained once → a STEER directive.

    A steer may carry a TRANSIENT consult attachment (U6) — e.g. a bug screenshot
    dropped in the comment thread. ``media`` is an opaque ref (a stored attachment
    path / url) and ``media_kind`` names the CONSULT-capable plugin (``screenshot``)
    that turns it into a ``Consult:`` line for the realizing agent. The attachment
    is consumed with the steer (drained-once) and never persisted as a block —
    that is the transient lifecycle (KTD4), reusing the steer channel, not new
    machinery. Identity is the (author-minted) ``comment_id`` (KTD4: id-scoped, not
    ``(feature_id, text)`` — two byte-identical notes stay distinct)."""
    feature_id: str
    text: str
    comment_id: str = ""  # the doc thread id (so the host can mark it sent)
    media: str = ""       # opaque attachment ref (transient consult media, U6)
    media_kind: str = ""  # CONSULT plugin key for the attachment (e.g. "screenshot")
    ts: int = 0           # unix ms


@dataclass
class BlockEdit:
    """A host edit to a typed-media block (U2), handed to Loop B for ``lower``
    dispatch. Keyed by the STABLE block id (KTD8) so identity is never inferred
    from content. ``action`` distinguishes the intent of the edit:

    - ``edit``   — content changed; dispatch the plugin's ``lower``.
    - ``add``    — a new block authored; dispatch ``lower`` (it may imply code).
    - ``remove`` — the block (its *projection*) was deleted. **Destructive
      asymmetry (KTD2): a removal NEVER auto-deletes code** — Loop B at most lets
      the plugin propose a change; by default it just drops the projection.

    A pure reorder (``ord`` change / move) is NOT a block-edit: it has no code
    effect, so the host does not emit one (e.g. moving an image)."""
    block_id: str
    feature_id: str
    kind: str
    action: str = "edit"      # "edit" | "add" | "remove"
    content: str = ""         # new content (empty for remove)
    prev_content: str = ""    # content before the edit (for the lower delta / diff)
    ts: int = 0               # unix ms


# The recognised command kinds (U3). Each maps onto a NodeOpKind in loop_b (the apply
# path); a command with any other kind is dropped on read. ``set_title``/``set_description``
# are description-level (SUGGEST-eligible, KTD10); ``add``/``move``/``retire`` are
# structural (HANDOFF-gated). Kept here so edits.py and dispatch.py agree on the set.
COMMAND_KINDS = frozenset({"add", "set_title", "set_description", "move", "retire"})


@dataclass
class Command:
    """An identity-keyed authored edit (U3 / KTD3) — the explicit op the webview
    emits instead of letting Loop B INFER it from a doc diff. Applied directly to
    the store via ``apply_op``; the doc-diff inference layer is retired (U7).

    * ``id`` is the IDEMPOTENCY key (KTD8): a stable, author-minted id recorded in
      the store's ``applied_commands`` ledger so a re-sent or replayed command
      (a drain interleaved with a crash) is a no-op on the second apply.
    * ``kind`` ∈ {``add``, ``set_title``, ``set_description``, ``move``, ``retire``}
      maps onto a ``NodeOpKind`` (add→ADD_NODE, set_title/set_description→AMEND,
      move→MOVE_NODE, retire→RETIRE_NODE).
    * ``feature_id`` targets an existing feature (empty for ``add``, which mints one).
    * ``local_id`` is the webview's client-side node id for ``add`` (KTD8): the
      minted fid is correlated back to it so the host adopts the right node — no
      title/order guessing, no duplicate/orphan add.
    * ``base_rev`` is the per-feature version the edit was authored from (the U5
      version gate uses it; recorded here for the channel, unused on the Phase-A
      apply path).
    * ``base_text`` is the value of the field this command REPLACES, as the author
      last knew it. ``None`` means "no claim" (a legacy or CLI-authored command)
      and applies unconditionally, exactly as before. When present, Loop B refuses
      to overwrite a feature whose stored text has moved since — see
      :func:`codoc.loop.loop_b._base_conflict`. It is the full text rather than a
      hash so the comparison uses ONE normalizer (the daemon's own) on both sides;
      a hash would require a byte-identical hash implementation in TypeScript and
      Python, and any drift between them would read as a conflict on every edit.
    * ``payload`` carries the kind's data: ``add`` → ``title``/``description``/
      ``parent_id``; ``set_title`` → ``title``; ``set_description`` →
      ``description``; ``move`` → ``parent_id``; ``retire`` → (nothing)."""
    id: str
    kind: str
    feature_id: str = ""
    local_id: str = ""
    base_rev: int = 0
    base_text: str | None = None
    session: str = ""   # the editing session that authored it — lets the daemon tell
                        # "I am continuing my own edit" from "someone else wrote here"
    payload: dict = field(default_factory=dict)


@dataclass
class Intent:
    id: str            # suggestion id (host-minted, e.g. "d-<fid>")
    feature_id: str
    actor: str = "human"
    ts: int = 0        # unix ms
    # The suggested text — present only for the field(s) the suggestion changes.
    # A payload-carrying intent is APPLIED by Loop B (the agent-side "apply",
    # classify row 9); a payload-less intent is hold-only. None = no change to
    # that field ("" is a real value: clear the description).
    title: str | None = None
    description: str | None = None


@dataclass
class Directive:
    id: str            # d-… (model.ids.new_directive_id)
    feature_id: str    # "" when unknown (e.g. ADD whose id wasn't recoverable)
    kind: str          # NodeOpKind value, or "steer" (an inline `> …` comment)
    caused_by: str = ""  # suggestion id or event id that queued this directive
    text: str = ""     # the rendered directive body — lets a later Loop B pass
                       # APPEND to an in-flight queue (rebuild realize.md from
                       # old + new) instead of clobbering unimplemented items
    baseline: str = ""  # the feature's description BEFORE this edit (AMEND only) — lets
                        # the IDE diff baseline↔current and underline the changed text
    handed_off: bool = True  # True = in realize.md / sent to the agent. False = a HELD
                             # DRAFT (in the manifest + the in-situ diff/hold set, but NOT
                             # yet realized) until an explicit hand-off (commit / steer /
                             # plan-flag / RETIRE-with-code / `codoc realize`). The
                             # held-draft model: Loop B's finalize derives this per-KIND —
                             # an AMEND/block edit is born held (constructed handed_off=False)
                             # and flips True only when its feature appears in the one-shot
                             # ``handoffs`` channel; an explicit gesture (steer/retire/plan)
                             # is handed off on mint. Once True it is STICKY (never demoted).
                             # Default True here so a LEGACY manifest entry (written before
                             # the held-draft model, lacking the field) still realizes.
    ts: int = 0        # unix ms when this directive was minted. Only a HELD DRAFT uses
                       # it, to expire its doc-wins hold (see :func:`hold_set`). 0 means
                       # "unknown" (a legacy entry) and never expires — an unknown age is
                       # not evidence of abandonment.


def edits_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / EDITS_FILENAME


def manifest_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / REALIZE_MANIFEST_FILENAME


def _load(codoc_dir: str | Path) -> dict:
    return read_json(edits_path(codoc_dir), default={})


def read_annotations(codoc_dir: str | Path) -> dict[str, EditAnnotation]:
    """Pending per-feature authorship annotations, keyed by feature_id.
    Last annotation per feature wins (the host appends; later = fresher)."""
    out: dict[str, EditAnnotation] = {}
    for e in _load(codoc_dir).get("edits", []):
        fid = e.get("feature_id")
        if fid:
            out[fid] = EditAnnotation(
                feature_id=fid,
                fields=list(e.get("fields") or []),
                actor=e.get("actor") or "human",
                mode=e.get("mode") or "pen",
                suggestion_id=e.get("suggestion_id") or "",
                ts=int(e.get("ts") or 0),
            )
    return out


def read_commands(codoc_dir: str | Path) -> list[Command]:
    """Pending identity-keyed authored commands (U3), order-preserving. A command
    needs an ``id`` (the idempotency key) and a recognised ``kind``; malformed
    entries are dropped (a stale/garbled command can at worst be skipped, never
    crash a pass). The ``payload`` is passed through verbatim for the applier."""
    out: list[Command] = []
    for c in _load(codoc_dir).get("commands", []):
        if not isinstance(c, dict):
            continue
        cid = c.get("id") or ""
        kind = c.get("kind") or ""
        if not cid or kind not in COMMAND_KINDS:
            continue
        payload = c.get("payload")
        out.append(Command(
            id=cid, kind=kind, feature_id=c.get("feature_id") or "",
            local_id=c.get("local_id") or "", base_rev=int(c.get("base_rev") or 0),
            base_text=c["base_text"] if isinstance(c.get("base_text"), str) else None,
            session=c.get("session") or "",
            payload=dict(payload) if isinstance(payload, dict) else {}))
    return out


def read_intents(codoc_dir: str | Path) -> list[Intent]:
    out: list[Intent] = []
    for i in _load(codoc_dir).get("intents", []):
        if i.get("feature_id"):
            out.append(Intent(id=i.get("id") or "", feature_id=i["feature_id"],
                              actor=i.get("actor") or "human", ts=int(i.get("ts") or 0),
                              title=i["title"] if "title" in i else None,
                              description=i["description"] if "description" in i else None))
    return out


# The edits.json lists. ``edits``/``cancellations``/``steers`` are loop-drained
# one-shot; ``intents`` and ``drafts`` are host-owned (the loops only read them).
# Every writer preserves the lists it isn't changing via ``_rewrite``.
#   ``drafts`` = feature ids the webview is holding as suggesting-mode DRAFTS: their
#   queued directive stays held (out of realize.md) until the human hands off. The host
#   adds a fid on a code-implying draft edit and removes it on hand-off; the loop derives
#   each directive's ``handed_off`` from this set every pass (so removing a fid releases
#   it). Empty/absent → every directive is handed off, i.e. today's immediate-realize.
#   ``commands`` = identity-keyed authored edits (U3): the explicit op the webview
#   emits (add/set_title/set_description/move/retire) instead of Loop B inferring it
#   from a doc diff. Loop-drained one-shot, applied via apply_op (KTD3); idempotent on
#   the store's applied-command-id ledger (KTD8). A v1 file (no key) reads as empty.
_LISTS = ("commands", "edits", "intents", "cancellations", "steers", "drafts", "block_edits", "handoffs")

# Cached, reentrant FileLock per repo guarding every edits.json read-modify-write.
_edit_locks: dict[str, object] = {}


def _edits_lock(codoc_dir: str | Path):
    """The shared cross-process lock for edits.json mutations.

    edits.json is no longer single-host: the ``codoc serve`` hub writes remote
    suggestions' intents/drafts/steers while the daemon drains them. An atomic
    write alone stops torn READS but not lost UPDATES across two read-modify-write
    cycles, so every mutator holds this lock across its read AND its write. The
    lock is cached per repo and reentrant, so a drain/append that internally calls
    the (also-locked) :func:`_rewrite` re-enters the same lock instead of
    deadlocking; the daemon shares it by using these same functions."""
    from filelock import FileLock

    key = str(Path(codoc_dir) / (EDITS_FILENAME + ".lock"))
    lock = _edit_locks.get(key)
    if lock is None:
        lock = FileLock(key, timeout=5)
        _edit_locks[key] = lock
    return lock


def _locked(fn):
    """Hold :func:`_edits_lock` across the decorated mutator's read + write."""
    from functools import wraps

    @wraps(fn)
    def wrapper(codoc_dir, *args, **kwargs):
        with _edits_lock(codoc_dir):
            return fn(codoc_dir, *args, **kwargs)

    return wrapper


@_locked
def _rewrite(codoc_dir: str | Path, **changes: list) -> Path | None:
    """Read edits.json, overlay the changed lists, write it back (or delete the file
    when every list is empty). One funnel so a drain/append never drops a sibling
    list. Returns the path written, or None when the file was removed."""
    data = _load(codoc_dir)
    merged = {k: list(changes[k] if k in changes else (data.get(k) or [])) for k in _LISTS}
    if not any(merged.values()):
        try:
            edits_path(codoc_dir).unlink()
        except FileNotFoundError:
            pass
        return None
    dest = edits_path(codoc_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {"version": EDITS_VERSION, "edits": merged["edits"], "intents": merged["intents"]}
    # Keep the optional lists out of the payload when empty (matches the prior shape
    # + keeps a plain annotations-only file byte-identical to before, modulo version).
    for k in ("commands", "cancellations", "steers", "drafts", "block_edits", "handoffs"):
        if merged[k]:
            payload[k] = merged[k]
    atomic_write_json(dest, payload)
    return dest


def _write_edits_file(
    codoc_dir: str | Path, *, edits: list, intents: list,
    cancellations: list | None = None, steers: list | None = None,
) -> Path | None:
    """Overwrite the named lists wholesale (the others reset to empty). The test +
    host-setup seam for seeding intents/edits; production drains/appends go through
    :func:`_rewrite`, which PRESERVES the lists it isn't changing."""
    return _rewrite(codoc_dir, edits=edits, intents=intents,
                    cancellations=cancellations or [], steers=steers or [])


@_locked
def drain_annotations(codoc_dir: str | Path) -> dict[str, EditAnnotation]:
    """Consume the ``edits`` list (returning it keyed by feature), KEEPING the
    host-owned ``intents`` + the one-shot ``cancellations``/``steers`` in place."""
    anns = read_annotations(codoc_dir)
    if anns:
        _rewrite(codoc_dir, edits=[])
    return anns


def drain_commands(codoc_dir: str | Path) -> list[Command]:
    """READ the pending ``commands`` (order-preserving) WITHOUT clearing the list.

    Crash-consistency (KTD8): the channel is cleared only by :func:`clear_commands`
    AFTER the caller has durably applied each command (claimed it on the store
    ledger). Clearing the whole list up front — the prior behavior — would lose any
    command not yet applied if the process died mid-pass; the store ledger then can't
    help because the command never reached it. So the read is now non-destructive and
    Loop B clears exactly the ids it successfully claimed+applied. (Name kept for
    callers; the one-shot guarantee now comes from the ledger + selective clear, not
    from draining on read.)"""
    return read_commands(codoc_dir)


@_locked
def clear_commands(codoc_dir: str | Path, applied_ids: set[str]) -> None:
    """Remove the commands whose ids are in ``applied_ids`` from the channel, rewriting
    the survivors back. Called AFTER Loop B has durably applied (ledger-claimed) those
    ids, so a crash mid-pass leaves the un-applied commands in the channel for re-run —
    the survivors are never lost. A no-op when nothing was applied."""
    if not applied_ids:
        return
    survivors = [c for c in (_load(codoc_dir).get("commands") or [])
                 if not (isinstance(c, dict) and (c.get("id") or "") in applied_ids)]
    _rewrite(codoc_dir, commands=survivors)


@_locked
def append_command(codoc_dir: str | Path, cmd: Command) -> Path | None:
    """Append an identity-keyed authored command (host emit affordance; CLI/tests).
    Preserves the other edits.json lists. Drained + applied by Loop B (U3)."""
    entry = {
        "id": cmd.id, "kind": cmd.kind, "feature_id": cmd.feature_id,
        "local_id": cmd.local_id, "base_rev": cmd.base_rev, "payload": dict(cmd.payload),
    }
    if cmd.base_text is not None:
        entry["base_text"] = cmd.base_text
    if cmd.session:
        entry["session"] = cmd.session
    commands = (_load(codoc_dir).get("commands") or []) + [entry]
    return _rewrite(codoc_dir, commands=commands)


def read_cancellations(codoc_dir: str | Path) -> list[str]:
    """Pending realize-withdrawals: feature ids whose queued directive the human
    asked to cancel (U6). Order-preserving, deduped."""
    out: list[str] = []
    seen: set[str] = set()
    for c in _load(codoc_dir).get("cancellations", []):
        fid = c.get("feature_id") if isinstance(c, dict) else None
        if fid and fid not in seen:
            seen.add(fid)
            out.append(fid)
    return out


@_locked
def drain_cancellations(codoc_dir: str | Path) -> list[str]:
    """Consume the ``cancellations`` list (feature ids), keeping the others — Loop B
    prunes the matching directives from the queue."""
    cancels = read_cancellations(codoc_dir)
    if cancels:
        _rewrite(codoc_dir, cancellations=[])
    return cancels


def read_steers(codoc_dir: str | Path) -> list[Steer]:
    """Pending inline-comment steers (U2b): the webview's `> …` comments handed to
    Loop B through edits.json (the host no longer writes them into tree.codoc)."""
    out: list[Steer] = []
    for s in _load(codoc_dir).get("steers", []):
        if isinstance(s, dict) and s.get("feature_id") and (s.get("text") or s.get("media")):
            out.append(Steer(feature_id=s["feature_id"], text=s.get("text") or "",
                             comment_id=s.get("comment_id") or "",
                             media=s.get("media") or "", media_kind=s.get("media_kind") or "",
                             ts=int(s.get("ts") or 0)))
    return out


@_locked
def drain_steers(codoc_dir: str | Path) -> list[Steer]:
    """Consume the ``steers`` list (one-shot), keeping the others — Loop B turns each
    into a STEER directive exactly once (no re-queue: the list is cleared here)."""
    steers = read_steers(codoc_dir)
    if steers:
        _rewrite(codoc_dir, steers=[])
    return steers


@_locked
def append_annotation(codoc_dir: str | Path, ann: EditAnnotation) -> Path | None:
    """Append a settle annotation (used by the CLI/tests; the IDE host writes
    this file too)."""
    edits = (_load(codoc_dir).get("edits") or []) + [{
        "feature_id": ann.feature_id, "fields": ann.fields, "actor": ann.actor,
        "mode": ann.mode, "suggestion_id": ann.suggestion_id,
        "ts": ann.ts or int(time.time() * 1000),
    }]
    return _rewrite(codoc_dir, edits=edits)


@_locked
def append_cancellation(codoc_dir: str | Path, feature_id: str) -> Path | None:
    """Append a realize-withdrawal request for ``feature_id`` (host withdraw
    affordance; CLI/tests). Drained by Loop B."""
    cancellations = (_load(codoc_dir).get("cancellations") or []) + [
        {"feature_id": feature_id, "ts": int(time.time() * 1000)}]
    return _rewrite(codoc_dir, cancellations=cancellations)


@_locked
def append_steer(codoc_dir: str | Path, steer: Steer) -> Path | None:
    """Append a one-shot inline-comment steer (U2b host comment-create; CLI/tests).
    Drained by Loop B into a STEER directive."""
    entry = {
        "feature_id": steer.feature_id, "text": steer.text,
        "comment_id": steer.comment_id, "ts": steer.ts or int(time.time() * 1000),
    }
    if steer.media:
        entry["media"] = steer.media
        entry["media_kind"] = steer.media_kind or "screenshot"
    steers = (_load(codoc_dir).get("steers") or []) + [entry]
    return _rewrite(codoc_dir, steers=steers)


def read_block_edits(codoc_dir: str | Path) -> list[BlockEdit]:
    """Pending typed-media block edits (U2): host edits to diagram/image/latex/url
    blocks, handed to Loop B for ``lower`` dispatch. Order-preserving."""
    out: list[BlockEdit] = []
    for b in _load(codoc_dir).get("block_edits", []):
        if isinstance(b, dict) and b.get("block_id") and b.get("feature_id"):
            out.append(BlockEdit(
                block_id=b["block_id"], feature_id=b["feature_id"],
                kind=b.get("kind") or "", action=b.get("action") or "edit",
                content=b.get("content") or "", prev_content=b.get("prev_content") or "",
                ts=int(b.get("ts") or 0)))
    return out


@_locked
def drain_block_edits(codoc_dir: str | Path) -> list[BlockEdit]:
    """Consume the ``block_edits`` list (one-shot), keeping the others — Loop B
    dispatches each through the block plugin's ``lower``."""
    edits = read_block_edits(codoc_dir)
    if edits:
        _rewrite(codoc_dir, block_edits=[])
    return edits


@_locked
def append_block_edit(codoc_dir: str | Path, edit: BlockEdit) -> Path | None:
    """Append a typed-media block edit (host edit affordance; CLI/tests). Drained by
    Loop B into a ``lower`` dispatch."""
    block_edits = (_load(codoc_dir).get("block_edits") or []) + [{
        "block_id": edit.block_id, "feature_id": edit.feature_id, "kind": edit.kind,
        "action": edit.action, "content": edit.content, "prev_content": edit.prev_content,
        "ts": edit.ts or int(time.time() * 1000),
    }]
    return _rewrite(codoc_dir, block_edits=block_edits)


def read_drafts(codoc_dir: str | Path) -> set[str]:
    """Feature ids the webview is holding as suggesting-mode drafts (host-owned).
    A directive for one of these stays held (out of realize.md) until hand-off."""
    out: set[str] = set()
    for d in _load(codoc_dir).get("drafts", []):
        fid = d.get("feature_id") if isinstance(d, dict) else d
        if isinstance(fid, str) and fid:
            out.add(fid)
    return out


def set_drafts(codoc_dir: str | Path, feature_ids: list[str]) -> Path | None:
    """Host/test seam: set the held-draft feature-id set wholesale (hand-off removes
    ids; a draft edit adds them). Preserves the other edits.json lists."""
    return _rewrite(codoc_dir, drafts=[{"feature_id": f} for f in feature_ids])


def read_handoffs(codoc_dir: str | Path) -> list[str]:
    """Pending hand-off requests: feature ids the human EXPLICITLY chose to realize
    (the webview's commit / ⌘S, or ``codoc realize``). This is the POSITIVE realize
    signal in the held-draft model — a doc AMEND mints a directive that stays held
    until its feature appears here. Order-preserving, deduped."""
    out: list[str] = []
    seen: set[str] = set()
    for h in _load(codoc_dir).get("handoffs", []):
        fid = h.get("feature_id") if isinstance(h, dict) else h
        if isinstance(fid, str) and fid and fid not in seen:
            seen.add(fid)
            out.append(fid)
    return out


@_locked
def drain_handoffs(codoc_dir: str | Path) -> list[str]:
    """Consume the ``handoffs`` list (feature ids), keeping the others. Loop B flips
    the matching held directives to handed_off=True (→ realize.md). One-shot: a
    hand-off is an event, not durable state — once a directive is handed off the
    manifest records it (handed_off is sticky there)."""
    handoffs = read_handoffs(codoc_dir)
    if handoffs:
        _rewrite(codoc_dir, handoffs=[])
    return handoffs


@_locked
def append_handoffs(codoc_dir: str | Path, feature_ids: list[str]) -> Path | None:
    """Host/CLI seam: append feature ids to the one-shot hand-off list (preserves the
    others). The webview's commit/hand-off and ``codoc realize`` both write here."""
    existing = read_handoffs(codoc_dir)
    merged = existing + [f for f in feature_ids if f and f not in existing]
    return _rewrite(codoc_dir, handoffs=[{"feature_id": f} for f in merged])


# ─── edits.host.jsonl — the IDE→daemon append-only op log (single-writer, U9) ──
#
# The IDE (a SEPARATE process) must never read-modify-write edits.json: it does not
# hold this module's cross-process lock, so its RMW could clobber the daemon's/hub's
# locked RMW (a lost command / hand-off / steer) and its fixed-tmp rename could ENOENT-
# crash against a concurrent writer. Instead the IDE APPENDS one op per line to
# ``edits.host.jsonl`` (O_APPEND is atomic per small write, so two IDE windows can even
# append concurrently), and the daemon MERGES those ops into ``edits.json`` under the
# lock at the start of every Loop B pass — replaying each op through the same
# ``append_*`` / ``set_drafts`` functions the daemon/hub already use, so all existing
# dedup/one-shot semantics carry over. Pure append means the IDE never re-includes an
# already-merged op, so nothing is double-fired on the happy path.

def host_ops_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / HOST_OPS_FILENAME


def append_host_op(codoc_dir: str | Path, fn: str, arg) -> Path:
    """Append one host op (``{"fn", "arg"}``) to ``edits.host.jsonl`` (the Python
    mirror of the TS host's append; used by the CLI/tests). Pure append — no lock, no
    read — so it never races the daemon's locked merge. The daemon consumes it via
    :func:`merge_host_ops`."""
    dest = host_ops_path(codoc_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "a", encoding="utf-8") as f:
        f.write(json.dumps({"fn": fn, "arg": arg}) + "\n")
    return dest


def _dispatch_host_op(codoc_dir: str | Path, fn: str, arg) -> bool:
    """Apply one host op to edits.json via the matching lock-guarded writer. Returns
    True when the op was recognised + applied. Unknown/garbled ops are skipped (a
    forward-compatible IDE op the daemon predates must not crash the merge)."""
    if fn == "appendCommand" and isinstance(arg, dict):
        append_command(codoc_dir, Command(
            id=arg.get("id") or "", kind=arg.get("kind") or "",
            feature_id=arg.get("feature_id") or "", local_id=arg.get("local_id") or "",
            base_rev=int(arg.get("base_rev") or 0),
            payload=arg.get("payload") if isinstance(arg.get("payload"), dict) else {}))
    elif fn == "appendSteer" and isinstance(arg, dict):
        append_steer(codoc_dir, Steer(
            feature_id=arg.get("feature_id") or "", text=arg.get("text") or "",
            comment_id=arg.get("comment_id") or "", media=arg.get("media") or "",
            media_kind=arg.get("media_kind") or "", ts=int(arg.get("ts") or 0)))
    elif fn == "appendBlockEdit" and isinstance(arg, dict):
        append_block_edit(codoc_dir, BlockEdit(
            block_id=arg.get("block_id") or "", feature_id=arg.get("feature_id") or "",
            kind=arg.get("kind") or "", action=arg.get("action") or "edit",
            content=arg.get("content") or "", prev_content=arg.get("prev_content") or "",
            ts=int(arg.get("ts") or 0)))
    elif fn == "appendCancellation" and isinstance(arg, dict) and arg.get("feature_id"):
        append_cancellation(codoc_dir, arg["feature_id"])
    elif fn == "appendHandoffs" and isinstance(arg, list):
        append_handoffs(codoc_dir, [f for f in arg if isinstance(f, str)])
    elif fn == "setDrafts" and isinstance(arg, list):
        set_drafts(codoc_dir, [f for f in arg if isinstance(f, str)])
    elif fn == "appendAnnotation" and isinstance(arg, dict) and arg.get("feature_id"):
        append_annotation(codoc_dir, EditAnnotation(
            feature_id=arg["feature_id"], fields=list(arg.get("fields") or []),
            actor=arg.get("actor") or "human", mode=arg.get("mode") or "pen",
            suggestion_id=arg.get("suggestion_id") or "", ts=int(arg.get("ts") or 0)))
    else:
        return False
    return True


def _drain_host_file(codoc_dir: str | Path, path: Path) -> int:
    """Apply every op line in ``path`` to edits.json, then delete ``path``. A garbled
    line or an unrecognised op is skipped (logged) — one bad line never blocks the rest.
    Called under :func:`_edits_lock`."""
    import logging

    applied = 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            op = json.loads(line)
            if isinstance(op, dict) and _dispatch_host_op(codoc_dir, op.get("fn") or "", op.get("arg")):
                applied += 1
        except Exception as exc:  # noqa: BLE001 — tolerate one bad line, keep merging
            logging.getLogger(__name__).warning(
                "codoc: skipping malformed host op (%s): %s", exc, line[:200])
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return applied


def merge_host_ops(codoc_dir: str | Path) -> int:
    """Merge the IDE's ``edits.host.jsonl`` append log into ``edits.json`` under the
    cross-process lock, so the daemon's read-modify-write and the IDE's writes can never
    interleave. Returns the number of ops applied (0 when nothing was pending).

    Atomic hand-off: rename the live log to a ``.merging`` sidecar so any concurrent IDE
    append lands in a fresh log for the NEXT pass; then replay the sidecar's ops. A
    ``.merging`` left by a crashed merge is recovered first (its ops may be partly
    applied — ``appendCommand`` is idempotent on the store ledger; the one-shot channels
    are low-stakes to re-append). Called at the top of every Loop B pass + at daemon
    startup so pending IDE edits are always absorbed before the store is read."""
    host = host_ops_path(codoc_dir)
    merging = Path(str(host) + ".merging")
    applied = 0
    with _edits_lock(codoc_dir):
        if merging.exists():                       # recover a crash-orphaned batch first
            applied += _drain_host_file(codoc_dir, merging)
        if host.exists():
            import os
            os.replace(host, merging)              # atomic; new appends go to a fresh log
            applied += _drain_host_file(codoc_dir, merging)
    return applied


# ─── realize.json — the directive manifest ───────────────────────────────────

def write_manifest(codoc_dir: str | Path, directives: list[Directive]) -> Path:
    dest = manifest_path(codoc_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(dest, {"version": 1, "directives": [
        {"id": d.id, "feature_id": d.feature_id, "kind": d.kind,
         "caused_by": d.caused_by, "text": d.text, "baseline": d.baseline,
         "handed_off": d.handed_off, "ts": d.ts}
        for d in directives
    ]})
    return dest


def _parse_manifest(data: dict) -> list[Directive]:
    return [Directive(id=d.get("id") or "", feature_id=d.get("feature_id") or "",
                      kind=d.get("kind") or "", caused_by=d.get("caused_by") or "",
                      text=d.get("text") or "", baseline=d.get("baseline") or "",
                      handed_off=bool(d.get("handed_off", True)),
                      ts=int(d.get("ts") or 0))
            for d in data.get("directives", [])]


def read_manifest(codoc_dir: str | Path) -> list[Directive]:
    """The queued directives. A manifest with no ``realize.md`` beside it is stale —
    the agent finished and deleted the queue — UNLESS it still holds DRAFT directives
    (``handed_off=False``), which intentionally live without a realize.md until the
    human hands them off. So: no realize.md + a held draft → keep; no realize.md + all
    handed-off → stale (drained: outcomes logged, manifest rewritten/cleared).

    The drain MUTATES, and this reader runs in several processes at once (the CC
    hook on every tool call, status refreshes, loop passes) — so the mutating
    branch double-checks under :func:`_edits_lock`: Loop B writes ``realize.md``
    BEFORE its manifest, so a fresh queue appearing in the race window is caught
    by the locked re-check instead of being clobbered by a stale drain."""
    path = manifest_path(codoc_dir)
    if not path.exists():
        return []
    directives = _parse_manifest(read_json(path, default={}))
    if (Path(codoc_dir) / REALIZE_FILENAME).exists():
        return directives
    with _edits_lock(codoc_dir):
        if (Path(codoc_dir) / REALIZE_FILENAME).exists():
            # A new queue landed while we waited for the lock — nothing is stale.
            return _parse_manifest(read_json(path, default={}))
        directives = _parse_manifest(read_json(path, default={}))
        drafts = [d for d in directives if not d.handed_off]
        done = [d for d in directives if d.handed_off]
        # The queue draining is the only completion signal a directive ever
        # emits — record it durably BEFORE the manifest entry vanishes, so
        # "what happened to my edit?" stays answerable (join realized.jsonl
        # against events.caused_by for the code changes it produced).
        if done:
            _log_realized(codoc_dir, done)
        if drafts:
            if done:
                write_manifest(codoc_dir, drafts)  # drop completed entries once
            return drafts  # held drafts survive without a realize.md
        clear_manifest(codoc_dir)
        return []


def clear_manifest(codoc_dir: str | Path) -> None:
    try:
        manifest_path(codoc_dir).unlink()
    except FileNotFoundError:
        pass


_REALIZED_LOG_MAX = 200  # bounded tail — old outcomes stop mattering


def _log_realized(codoc_dir: str | Path, directives: list[Directive]) -> None:
    """Append completed directives to ``realized.jsonl`` (idempotent by id).

    Best-effort: the log is a feedback surface, not a correctness channel, so
    IO errors are swallowed rather than blocking the drain."""
    import time as _time
    from datetime import datetime, timezone

    path = Path(codoc_dir) / REALIZED_LOG_FILENAME
    try:
        seen: set[str] = set()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    seen.add(json.loads(line).get("id") or "")
                except (ValueError, TypeError):
                    continue
        fresh = [d for d in directives if d.id and d.id not in seen]
        if not fresh:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        now_ts = _time.time()
        with open(path, "a", encoding="utf-8") as fh:
            for d in fresh:
                fh.write(json.dumps({
                    "id": d.id, "feature_id": d.feature_id, "kind": d.kind,
                    "caused_by": d.caused_by, "text": d.text,
                    "completed_at": now_iso, "ts": now_ts,
                }, ensure_ascii=False) + "\n")
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > _REALIZED_LOG_MAX:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text("\n".join(lines[-_REALIZED_LOG_MAX:]) + "\n",
                           encoding="utf-8")
            tmp.replace(path)
    except OSError:
        pass


def read_realized(codoc_dir: str | Path, limit: int = 50) -> list[dict]:
    """Recent directive outcomes, newest last — the durable answer to "what
    happened to my edit" after the realize queue drained."""
    path = Path(codoc_dir) / REALIZED_LOG_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict] = []
    for line in raw.splitlines():
        try:
            e = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(e, dict) and e.get("id"):
            out.append(e)
    return out[-limit:]


# ─── doc-wins hold set (classify table row 13) ───────────────────────────────

def hold_set(codoc_dir: str | Path, *, now_ms: int | None = None) -> set[str]:
    """Feature ids with pending doc-ahead intent: live suggestions (``intents``)
    ∪ queued directives (``realize.json``). Code-side AMEND/RETIRE/MOVE proposals
    on these features are suppressed until the hold releases — doc always wins.

    Two kinds of hold, with different lifetimes, because they mean different things:

    * A HANDED-OFF directive is work an agent is doing. It holds until the queue
      drains, however long that takes — releasing early would let Loop A rewrite a
      feature out from under a running agent.
    * A HELD DRAFT is an edit the author made and has not handed off. It expires.
      Without that backstop, tweaking a description and never pressing hand-off held
      the feature FOREVER: Loop A could no longer propose an amend, retire or move on
      it, and never badged it as drifted, so the feature quietly stopped tracking its
      code for the life of the repository. Nothing surfaced, and each such edit
      subtracted one more feature from the tree's usefulness.

    Same reasoning, and the same window, as an abandoned intent.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    held: set[str] = set()
    for i in read_intents(codoc_dir):
        if i.ts and now - i.ts > INTENT_STALE_MS:
            continue  # abandoned suggestion — backstop against a forever-hold
        held.add(i.feature_id)
    for d in read_manifest(codoc_dir):
        if not d.feature_id:
            continue
        if not d.handed_off and d.ts and now - d.ts > INTENT_STALE_MS:
            continue  # abandoned draft — the same backstop, for the same reason
        held.add(d.feature_id)
    return held


# ─── drift.json — the loop-computed per-feature drift/trust signal ────────────
#
# render.py:write_sidecar has NO live index, so it cannot compare a binding's
# fingerprint against the live tokens_hash. The loop passes that DO re-index
# (run_loop_a / reconcile_drift) compute the typed drift and persist it here;
# write_sidecar re-emits it passively as the sidecar's `feature_drift` slice —
# the exact pattern `holds` (a control-file read) reaches the sidecar by. An
# interactive write (Accept/Reject, MCP reflect) thus re-emits the last
# loop-computed drift unchanged rather than recomputing against a stale index.
#
# Only `questioned` / `binding-lost` features are stored; `followed` (the common
# case) is the ABSENCE of an entry — no badge.

# The two recorded drift states. "followed" is never written (absence = followed
# = no badge); "refreshed" is deliberately dropped — a REFRESH overwrites the
# binding fingerprint so a refreshed binding is indistinguishable from followed.
DRIFT_QUESTIONED = "questioned"      # realized feature owns a modified bound chunk, prose not amended
DRIFT_BINDING_LOST = "binding-lost"  # realized feature lost its last binding


def drift_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / DRIFT_FILENAME


def write_drift(codoc_dir: str | Path, drift: dict[str, str]) -> Path:
    """Persist the loop-computed per-feature drift map (only ``questioned`` /
    ``binding-lost`` entries; ``followed`` is the absence of an entry).

    Always written — an empty map clears a stale prior signal so a feature that
    re-followed (its prose was amended, or its binding came back) loses its
    badge on the next pass."""
    dest = drift_path(codoc_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(dest, {"version": 1, "drift": dict(drift)})
    return dest


def read_drift(codoc_dir: str | Path) -> dict[str, str]:
    """The last loop-computed drift map (``feature_id → state``). Tolerant:
    a missing or corrupt file degrades to ``{}`` (no badges)."""
    data = read_json(drift_path(codoc_dir), default={})
    out = data.get("drift") if isinstance(data, dict) else None
    return dict(out) if isinstance(out, dict) else {}


def merge_drift(
    codoc_dir: str | Path,
    fresh: dict[str, str],
    *,
    in_scope: set[str],
) -> Path:
    """Persist drift from a SCOPED pass without wiping out-of-scope entries.

    A scoped loop pass (the watch daemon's ``file_scope=code_files``) only
    re-examines features that own a binding in scope. Full-replacing
    ``drift.json`` would clear a still-valid badge on a feature bound entirely to
    a file the pass never touched. So we MERGE: read the existing map, drop only
    the entries for features that WERE re-examined this pass (``in_scope`` — those
    are now authoritatively re-derived in ``fresh``, including their absence =
    cleared), then overlay ``fresh``. Out-of-scope entries survive untouched.

    ``write_drift`` (full-replace) remains the right call for an unscoped pass,
    where every feature is re-examined and a stale entry SHOULD be cleared."""
    merged = {fid: state for fid, state in read_drift(codoc_dir).items()
              if fid not in in_scope}
    merged.update(fresh)
    return write_drift(codoc_dir, merged)


# ─── resolution.json — the loop-computed realize-divergence signal (U5) ───────
#
# When a realize epoch is active (a manifest is queued), Loop A classifies each
# directive's realization (divergence.classify_realization) and records the
# DIVERGENT targets here — ``{target_feature_id: "scope"|"intent"}``. A FAITHFUL
# realization is the ABSENCE of an entry: its badge simply clears when the queue
# drains, no review surface (F2). A divergent one keeps an entry so the sidecar
# re-emits it as ``feature_resolution`` and the IDE flags "review what the AI did"
# on top of the surfaced proposals (F3). Cleared (written empty) on any pass with
# no active epoch, so a stale signal never lingers past its directive.

def resolution_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / RESOLUTION_FILENAME


def write_resolution(codoc_dir: str | Path, divergent: dict[str, str]) -> Path:
    """Persist the realize-divergence map (``target_feature_id → reason``); only
    divergent targets are stored. Always written (an empty map clears a stale
    signal once the epoch that raised it has drained)."""
    dest = resolution_path(codoc_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(dest, {"version": 1, "divergent": dict(divergent)})
    return dest


def read_resolution(codoc_dir: str | Path) -> dict[str, str]:
    """The last loop-computed realize-divergence map (``feature_id → reason``).
    Tolerant: a missing/corrupt file degrades to ``{}`` (no review flags)."""
    data = read_json(resolution_path(codoc_dir), default={})
    out = data.get("divergent") if isinstance(data, dict) else None
    return dict(out) if isinstance(out, dict) else {}
