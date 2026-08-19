"""The timeline transport — `.codoc/revisions.json` (W8).

## What this is for

The tree is edited by three parties at once and nobody sees the whole of it happen.
A description you wrote on Tuesday is prose the loop refreshed on Wednesday and an
agent rewrote on Thursday, and the document only ever shows Thursday. `codoc history`
answers "who touched this feature" one feature at a time, in a terminal, in a list —
which is the wrong shape for the actual question, because the actual question is
*"what did this page say before, and why does it say this now?"*

This module ships the data that lets the editor answer that **in place**: scrub back
and the document reads as it did, with the change made at that moment marked in the
prose where it happened.

## Why a window of events, and not snapshots

The obvious design is to store a snapshot of the tree per revision. It is also the
wrong one: snapshots are O(tree) per change for a change that is O(paragraph), and
they go stale against the live store the moment anything else writes.

Instead this ships the CHANGES — each applied event with the text it displaced
(``NodeOp.prev_*``, recorded at the write boundary in ``loop.apply``) — and the
reconstruction runs in the editor, BACKWARDS from the live document it is already
holding. That inverts the cost (a revision costs what its change cost) and it makes
scrubbing local: dragging a timeline cannot afford a round trip per frame, and there
is no request channel to a daemon anyway. The reconstructor is
``vscode-codoc/src/state/revision-model.ts``.

## Why its own file rather than a sidecar slice

`tree.bindings.json` is read on every pass by everything, and this is the one slice
that carries PROSE. Folding it in would put the tree's whole edit history into the
hot path of every render, for a stance that is off by default. It is derived and
rebuildable exactly like the sidecar — delete it and the next pass writes it again —
and it is written from the same shared event scan, so it costs one JSON dump, not one
database read.

## What it does NOT do

It never claims to reconstruct what it cannot. An event written before ``prev_title``
existed records the new title and nothing else, and there is no backfill — inventing
a prior value the ledger never saw would make the timeline confidently wrong, which is
strictly worse than a timeline that says "this change can't be reconstructed". Entries
carry only what was really recorded, and the reader reports the gap.
"""
from __future__ import annotations

import json
from pathlib import Path

from codoc.loop.filenames import REVISIONS_FILENAME
from codoc.loop.fsio import atomic_write_json, read_json
from codoc.model.event import Event, NodeOpKind

# How many applied events reach the editor. The scan window upstream is 300 (shared with
# the changes + blame feeds); this caps what is SERIALIZED, because these entries carry
# prose and the others do not. 150 is roughly a week of active editing on this repo —
# far enough back to answer "what did this say before?", short enough that the file
# stays a few hundred KB at worst.
REVISION_LIMIT = 150

# REFRESH is excluded outright. It recomputes a fingerprint every time a bound symbol is
# touched, so it is by far the most numerous event kind and it changes nothing a reader
# can see — including it would bury the changes that matter under machine bookkeeping and
# make the scrubber's ticks meaningless. The same triage `render._auto_edits` documents.
_HIDDEN_KINDS = frozenset({NodeOpKind.REFRESH})

_RATIONALE_CAP = 240


def revisions_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / REVISIONS_FILENAME


def _entry(e: Event) -> dict:
    """One applied event as a timeline revision.

    Every field is presence-keyed: absent means "this op did not touch that", which is
    exactly what the reconstructor needs to know (a description-only amend must leave
    the title alone, and a `title: null` would be indistinguishable from "cleared it").
    """
    op = e.op
    out: dict = {
        "event_id": e.id,
        "at": e.at.to_str(),
        "kind": op.kind.value,
        "feature_id": op.feature_id or "",
        "actor": e.actor,
        "mode": e.mode,
    }
    if e.caused_by:
        out["caused_by"] = e.caused_by
    if op.rationale:
        r = op.rationale.strip()
        out["rationale"] = (r[:_RATIONALE_CAP] + "…") if len(r) > _RATIONALE_CAP else r
    # The text this op wrote, and the text it displaced. Both sides are needed: forward
    # for "what does it say now", backward for "what did it say then".
    if op.title is not None:
        out["title"] = op.title
    if op.description is not None:
        out["description"] = op.description
    if op.prev_title is not None:
        out["prev_title"] = op.prev_title
    if op.prev_description is not None:
        out["prev_description"] = op.prev_description
    if op.prev_written_by:
        out["prev_written_by"] = op.prev_written_by
    if op.parent_id is not None:
        out["parent_id"] = op.parent_id
    if op.prev_parent_id is not None:
        out["prev_parent_id"] = op.prev_parent_id
    # Code attribution changes carry no prose, but they ARE what the reader means by
    # "codoc bound this feature to that code" — the symbol paths make the entry legible
    # and give the code-diff surface something to open.
    if op.bindings:
        out["bindings"] = [f"{f}::{s}" if s else f for f, s in op.bindings]
    return out


def _directives_for(codoc_dir: str | Path, cause_ids: set[str]) -> dict[str, dict]:
    """The directives the emitted revisions cite, keyed by id.

    A directive is where a change's WHY lives — the author's prompt, the session it was
    typed in, the commit the code work started from. The join is done here, once, because
    this is the only place both halves are in hand: the live queue (``realize.json``) and
    the completed log (``realized.jsonl``) are separate files with separate lifetimes, and
    a directive migrates from one to the other the moment its work lands. Asking the
    editor to know that would leak the queue's lifecycle into a view layer.

    Completed entries are read FIRST and live ones layered over them, so a directive
    caught mid-migration (present in both) reports as still queued rather than flickering
    between the two on consecutive passes.

    The live read is ``peek_manifest``, never ``read_manifest``: the latter DRAINS a
    stale queue as a side effect of reading it, and building a view is not allowed to
    close the work it is describing.
    """
    if not cause_ids:
        return {}
    from codoc.loop import edits as edits_channel

    out: dict[str, dict] = {}

    def _put(d: dict, *, done: bool) -> None:
        did = str(d.get("id") or "")
        if not did or did not in cause_ids:
            return
        row = {"id": did, "kind": str(d.get("kind") or ""),
               "feature_id": str(d.get("feature_id") or ""),
               "text": str(d.get("text") or ""), "done": done}
        for key in ("asked", "session_id", "base_sha"):
            val = str(d.get(key) or "")
            if val:
                row[key] = val
        if done and d.get("completed_at"):
            row["completed_at"] = str(d["completed_at"])
        out[did] = row

    try:
        for d in edits_channel.read_realized(codoc_dir, limit=REVISION_LIMIT):
            _put(d, done=True)
    except Exception:  # noqa: BLE001 — provenance is advisory; never block a render
        pass
    try:
        for d in edits_channel.peek_manifest(codoc_dir):
            _put({"id": d.id, "kind": d.kind, "feature_id": d.feature_id, "text": d.text,
                  "asked": d.asked, "session_id": d.session_id, "base_sha": d.base_sha},
                 done=False)
    except Exception:  # noqa: BLE001
        pass
    return out


def build_revisions(events: list[Event], codoc_dir: str | Path) -> dict:
    """The `revisions.json` document for a newest-first window of events."""
    entries: list[dict] = []
    for e in events:
        if not e.applied or e.op.kind in _HIDDEN_KINDS:
            continue
        entries.append(_entry(e))
        if len(entries) >= REVISION_LIMIT:
            break
    causes = {c for c in (x.get("caused_by") for x in entries) if c}
    return {
        "version": 1,
        # Newest first, matching every other feed the IDE consumes.
        "revisions": entries,
        "directives": _directives_for(codoc_dir, causes),
        # True when the window is full, i.e. the tree has history older than this file
        # carries. The editor says so at the far end of the timeline rather than
        # implying the oldest entry is the beginning of the world.
        "truncated": len(entries) >= REVISION_LIMIT,
    }


def _same_window(prior: dict, doc: dict) -> bool:
    """Is the file on disk already this exact window?

    Compares the FULL id sequence, not just the head. Comparing only the newest id (plus
    a length) looked sufficient — a new event is a new head — and is not: HLCs are minted
    by several processes (the daemon, the MCP server, the CLI), so a clock-skewed event
    can sort BELOW the current head. At the window cap it then lands mid-list and pushes
    the oldest entry out, leaving the head and the length both unchanged. The write was
    skipped and the event never reached the file.

    The sequence is cheap to compare (ids only, no prose) and it cannot miss a change:
    any insertion, drop, or reorder alters it.
    """
    old = prior.get("revisions")
    if not isinstance(old, list):
        return False
    if prior.get("directives") != doc["directives"]:
        return False
    new = doc["revisions"]
    if len(old) != len(new):
        return False
    return all(a.get("event_id") == b.get("event_id") for a, b in zip(old, new))


def write_revisions(events: list[Event], codoc_dir: str | Path) -> None:
    """Write `revisions.json`, skipping the write when nothing changed.

    Called from ``write_sidecar`` with the event scan it has already done, so this adds
    no database read to a pass. The skip matters because the render pass runs on every
    save and most saves apply no event at all: comparing the newest event id and the
    entry count is enough to recognise an unchanged window, and it keeps the file's mtime
    stable so the extension's file-watch doesn't wake for a byte-identical rewrite.

    Best-effort throughout. A timeline that fails to write costs the reader a view; it
    must never cost them a render pass.
    """
    try:
        doc = build_revisions(events, codoc_dir)
        path = revisions_path(codoc_dir)
        prior = read_json(path, default=None)
        if isinstance(prior, dict) and _same_window(prior, doc):
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, doc)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
