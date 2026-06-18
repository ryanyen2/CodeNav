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
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from codoc.loop.filenames import (
    DRIFT_FILENAME,
    EDITS_FILENAME,
    REALIZE_FILENAME,
    REALIZE_MANIFEST_FILENAME,
    RESOLUTION_FILENAME,
)
from codoc.loop.fsio import atomic_write_json, read_json

# Intents older than this are ignored by the hold set (an abandoned suggestion
# must not hold a feature forever). The host clears satisfied intents itself;
# this is only the backstop. Timestamps are unix epoch MILLISECONDS (Date.now()).
INTENT_STALE_MS = 7 * 24 * 3600 * 1000


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
    (the host no longer writes tree.codoc). Drained once → a STEER directive."""
    feature_id: str
    text: str
    comment_id: str = ""  # the doc thread id (so the host can mark it sent)
    ts: int = 0           # unix ms


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
    handed_off: bool = True  # False = a DRAFT held in suggesting mode (in the manifest +
                             # the in-situ diff/hold set, but NOT yet in realize.md / sent
                             # to the agent) until the human hands it off. Default True so
                             # legacy manifests + non-suggesting (raw-text) edits realize
                             # immediately, exactly as before — the draft gate is additive.


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
_LISTS = ("edits", "intents", "cancellations", "steers", "drafts")


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
    payload: dict = {"version": 1, "edits": merged["edits"], "intents": merged["intents"]}
    # Keep the optional lists out of the payload when empty (matches the prior shape
    # + keeps a plain annotations-only file byte-identical to before).
    for k in ("cancellations", "steers", "drafts"):
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


def drain_annotations(codoc_dir: str | Path) -> dict[str, EditAnnotation]:
    """Consume the ``edits`` list (returning it keyed by feature), KEEPING the
    host-owned ``intents`` + the one-shot ``cancellations``/``steers`` in place."""
    anns = read_annotations(codoc_dir)
    if anns:
        _rewrite(codoc_dir, edits=[])
    return anns


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
        if isinstance(s, dict) and s.get("feature_id") and s.get("text"):
            out.append(Steer(feature_id=s["feature_id"], text=s["text"],
                             comment_id=s.get("comment_id") or "", ts=int(s.get("ts") or 0)))
    return out


def drain_steers(codoc_dir: str | Path) -> list[Steer]:
    """Consume the ``steers`` list (one-shot), keeping the others — Loop B turns each
    into a STEER directive exactly once (no re-queue: the list is cleared here)."""
    steers = read_steers(codoc_dir)
    if steers:
        _rewrite(codoc_dir, steers=[])
    return steers


def append_annotation(codoc_dir: str | Path, ann: EditAnnotation) -> Path | None:
    """Append a settle annotation (used by the CLI/tests; the IDE host writes
    this file too)."""
    edits = (_load(codoc_dir).get("edits") or []) + [{
        "feature_id": ann.feature_id, "fields": ann.fields, "actor": ann.actor,
        "mode": ann.mode, "suggestion_id": ann.suggestion_id,
        "ts": ann.ts or int(time.time() * 1000),
    }]
    return _rewrite(codoc_dir, edits=edits)


def append_cancellation(codoc_dir: str | Path, feature_id: str) -> Path | None:
    """Append a realize-withdrawal request for ``feature_id`` (host withdraw
    affordance; CLI/tests). Drained by Loop B."""
    cancellations = (_load(codoc_dir).get("cancellations") or []) + [
        {"feature_id": feature_id, "ts": int(time.time() * 1000)}]
    return _rewrite(codoc_dir, cancellations=cancellations)


def append_steer(codoc_dir: str | Path, steer: Steer) -> Path | None:
    """Append a one-shot inline-comment steer (U2b host comment-create; CLI/tests).
    Drained by Loop B into a STEER directive."""
    steers = (_load(codoc_dir).get("steers") or []) + [{
        "feature_id": steer.feature_id, "text": steer.text,
        "comment_id": steer.comment_id, "ts": steer.ts or int(time.time() * 1000),
    }]
    return _rewrite(codoc_dir, steers=steers)


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


# ─── realize.json — the directive manifest ───────────────────────────────────

def write_manifest(codoc_dir: str | Path, directives: list[Directive]) -> Path:
    dest = manifest_path(codoc_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(dest, {"version": 1, "directives": [
        {"id": d.id, "feature_id": d.feature_id, "kind": d.kind,
         "caused_by": d.caused_by, "text": d.text, "baseline": d.baseline,
         "handed_off": d.handed_off}
        for d in directives
    ]})
    return dest


def read_manifest(codoc_dir: str | Path) -> list[Directive]:
    """The queued directives. A manifest with no ``realize.md`` beside it is stale —
    the agent finished and deleted the queue — UNLESS it still holds DRAFT directives
    (``handed_off=False``), which intentionally live without a realize.md until the
    human hands them off. So: no realize.md + a held draft → keep; no realize.md + all
    handed-off → stale (cleared)."""
    path = manifest_path(codoc_dir)
    if not path.exists():
        return []
    data = read_json(path, default={})
    directives = [Directive(id=d.get("id") or "", feature_id=d.get("feature_id") or "",
                            kind=d.get("kind") or "", caused_by=d.get("caused_by") or "",
                            text=d.get("text") or "", baseline=d.get("baseline") or "",
                            handed_off=bool(d.get("handed_off", True)))
                  for d in data.get("directives", [])]
    if not (Path(codoc_dir) / REALIZE_FILENAME).exists():
        drafts = [d for d in directives if not d.handed_off]
        if drafts:
            return drafts  # held drafts survive without a realize.md
        clear_manifest(codoc_dir)
        return []
    return directives


def clear_manifest(codoc_dir: str | Path) -> None:
    try:
        manifest_path(codoc_dir).unlink()
    except FileNotFoundError:
        pass


# ─── doc-wins hold set (classify table row 13) ───────────────────────────────

def hold_set(codoc_dir: str | Path, *, now_ms: int | None = None) -> set[str]:
    """Feature ids with pending doc-ahead intent: live suggestions (``intents``)
    ∪ queued directives (``realize.json``). Code-side AMEND/RETIRE/MOVE proposals
    on these features are suppressed until the hold releases — doc always wins."""
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    held: set[str] = set()
    for i in read_intents(codoc_dir):
        if i.ts and now - i.ts > INTENT_STALE_MS:
            continue  # abandoned suggestion — backstop against a forever-hold
        held.add(i.feature_id)
    for d in read_manifest(codoc_dir):
        if d.feature_id:
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
