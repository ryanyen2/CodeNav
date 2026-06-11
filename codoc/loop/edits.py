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
                    "actor": "human", "ts": 0}]}

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

from codoc.loop.filenames import EDITS_FILENAME, REALIZE_FILENAME, REALIZE_MANIFEST_FILENAME
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
    kind: str          # NodeOpKind value
    caused_by: str = ""  # suggestion id or event id that queued this directive


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


def drain_annotations(codoc_dir: str | Path) -> dict[str, EditAnnotation]:
    """Consume the ``edits`` list (returning it keyed by feature), KEEPING the
    ``intents`` list in place — intents are owned by the host, not the loop."""
    anns = read_annotations(codoc_dir)
    if not anns:
        return anns
    data = _load(codoc_dir)
    intents = data.get("intents") or []
    if intents:
        _write_edits_file(codoc_dir, edits=[], intents=intents)
    else:
        try:
            edits_path(codoc_dir).unlink()
        except FileNotFoundError:
            pass
    return anns


def _write_edits_file(codoc_dir: str | Path, *, edits: list, intents: list) -> Path:
    dest = edits_path(codoc_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(dest, {"version": 1, "edits": edits, "intents": intents})
    return dest


def append_annotation(codoc_dir: str | Path, ann: EditAnnotation) -> Path:
    """Append a settle annotation (used by the CLI/tests; the IDE host writes
    this file too)."""
    data = _load(codoc_dir)
    edits = data.get("edits") or []
    edits.append({
        "feature_id": ann.feature_id, "fields": ann.fields, "actor": ann.actor,
        "mode": ann.mode, "suggestion_id": ann.suggestion_id,
        "ts": ann.ts or int(time.time() * 1000),
    })
    return _write_edits_file(codoc_dir, edits=edits, intents=data.get("intents") or [])


# ─── realize.json — the directive manifest ───────────────────────────────────

def write_manifest(codoc_dir: str | Path, directives: list[Directive]) -> Path:
    dest = manifest_path(codoc_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(dest, {"version": 1, "directives": [
        {"id": d.id, "feature_id": d.feature_id, "kind": d.kind, "caused_by": d.caused_by}
        for d in directives
    ]})
    return dest


def read_manifest(codoc_dir: str | Path) -> list[Directive]:
    """The queued directives. A manifest with no ``realize.md`` beside it is
    stale (the agent deleted the queue but not the manifest) — ignored and
    opportunistically removed."""
    path = manifest_path(codoc_dir)
    if not path.exists():
        return []
    if not (Path(codoc_dir) / REALIZE_FILENAME).exists():
        clear_manifest(codoc_dir)
        return []
    data = read_json(path, default={})
    return [Directive(id=d.get("id") or "", feature_id=d.get("feature_id") or "",
                      kind=d.get("kind") or "", caused_by=d.get("caused_by") or "")
            for d in data.get("directives", [])]


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
