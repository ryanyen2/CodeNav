"""payload.py — derive the browser DocPayload from .codoc/* (U3).

The ``codoc serve`` hub serves a remote SUGGEST surface. This assembles the
``DocPayload`` (the ``protocol.ts`` wire shape) that surface consumes — purely
from the daemon's file outputs: the ``tree.bindings.json`` sidecar, ``status.json``,
``tree.doc.json``, and the ``edits.json`` drafts. It does NOT recompute what the
daemon already derived — the sidecar IS the daemon's pure derived state, so this
RE-SHAPES it into the wire shape (the same sidecar the VS Code reader consumes,
so there is no second derivation to drift).

The VS Code editor's own ``buildPayload`` is intentionally left untouched: it
mixes in webview-only in-memory editor state (un-settled docAhead) that a fresh
browser load does not have. Tier 1 derives the browser payload independently.

Every read is tolerant (a missing/corrupt control file degrades to a sane empty
payload), so a hub serving a freshly-`init`ed or mid-write repo never crashes a
connected browser.
"""
from __future__ import annotations

from pathlib import Path

from codoc.codoc_file.render import BINDINGS_FILENAME
from codoc.loop.fsio import read_json
from codoc.loop.status import STATUS_FILENAME
from codoc.model.hlc import HLC

_DOC_FILENAME = "tree.doc.json"
# A wall-clock-ms HLC leaves ample room below this shift for the logical counter.
_LOGICAL_BITS = 20


def _sidecar(codoc_dir: str | Path) -> dict:
    return read_json(Path(codoc_dir) / BINDINGS_FILENAME, default={}) or {}


def _status(codoc_dir: str | Path) -> dict:
    return read_json(Path(codoc_dir) / STATUS_FILENAME, default={}) or {}


def _doc(codoc_dir: str | Path):
    return read_json(Path(codoc_dir) / _DOC_FILENAME, default=None)


def payload_version(codoc_dir: str | Path) -> int:
    """A monotonic, restart-safe version for the SSE drop-stale guard.

    Derived from the daemon's HLC stamp on ``status.json`` (``at``), which advances
    every loop pass and is wall-clock-based — so it never regresses across a server
    restart (the failure mode of a per-process counter the architecture review
    flagged). Mapped to a sortable int: wall-clock ms shifted left of the logical
    tie-breaker. Absent/corrupt status → 0."""
    at = _status(codoc_dir).get("at")
    if not isinstance(at, str):
        return 0
    try:
        h = HLC.from_str(at)
    except ValueError:
        return 0
    return (h.wall_clock << _LOGICAL_BITS) + h.logical_time


def _nodes_from_sidecar(sidecar: dict) -> tuple[dict, list[str]]:
    """Build the UINode map + roots from the sidecar's ``features`` + ``by_feature``.

    Cycle-safe (a malformed parent chain can't recurse forever) and order-stable
    (siblings + roots sorted by title) so the browser tree renders deterministically."""
    features = sidecar.get("features") or {}
    by_feature = sidecar.get("by_feature") or {}

    nodes: dict[str, dict] = {}
    for fid, meta in features.items():
        if not isinstance(meta, dict):
            continue
        binds = [
            {"file": b.get("file", ""), "symbol": b.get("symbol", "")}
            for b in (by_feature.get(fid) or [])
            if isinstance(b, dict)
        ]
        nodes[fid] = {
            "id": fid,
            "title": meta.get("title") or "",
            "parent_id": meta.get("parent_id"),
            "retired": False,  # the sidecar lists live features only
            "realized": bool(meta.get("realized", True)),
            "refCount": len(binds),
            "bindings": binds,
            "proposal": None,
            "depth": 0,
            "children": [],
            "activeMode": None,
        }

    children: dict[str, list[str]] = {}
    for fid, n in nodes.items():
        pid = n["parent_id"]
        if pid in nodes and pid != fid:
            children.setdefault(pid, []).append(fid)

    def _key(fid: str) -> str:
        return nodes[fid]["title"].lower()

    roots = sorted(
        [fid for fid, n in nodes.items() if n["parent_id"] not in nodes or n["parent_id"] == fid],
        key=_key,
    )

    seen: set[str] = set()

    def assign(fid: str, depth: int) -> None:
        if fid in seen:
            return  # cycle guard
        seen.add(fid)
        nodes[fid]["depth"] = depth
        kids = sorted(children.get(fid, []), key=_key)
        nodes[fid]["children"] = kids
        for k in kids:
            assign(k, depth + 1)

    for r in roots:
        assign(r, 0)
    # Any node never reached (orphaned by a cycle) still appears flat at depth 0.
    for fid in nodes:
        if fid not in seen:
            nodes[fid]["children"] = sorted(children.get(fid, []), key=_key)

    return nodes, roots


def build_browser_payload(codoc_dir: str | Path) -> dict:
    """The full DocPayload for the browser suggest surface, derived from files."""
    codoc_dir = Path(codoc_dir)
    sidecar = _sidecar(codoc_dir)
    status = _status(codoc_dir)
    features = sidecar.get("features") or {}
    holds = sorted(str(h) for h in (sidecar.get("holds") or []))

    from codoc.loop.edits import read_drafts

    draft_set = read_drafts(codoc_dir)
    drafts = sorted(set(holds) & draft_set)

    nodes, roots = _nodes_from_sidecar(sidecar)
    pitches = {
        fid: (meta.get("pitch") or "")
        for fid, meta in features.items()
        if isinstance(meta, dict)
    }
    state = status.get("state") or "in_sync"
    pending = int(status.get("pending") or 0)

    return {
        "nodes": nodes,
        "roots": roots,
        "status": {"state": state, "pending": pending},
        "sync": {
            "state": state,
            "pending": pending,
            "activeWrite": [],
            "activeRead": [],
            "phase": {},
        },
        "rootName": codoc_dir.parent.name,
        "pendingEventIds": [],
        "doc": _doc(codoc_dir),
        "awaitingAI": holds,
        "holdDetail": sidecar.get("hold_detail") or {},
        "drafts": drafts,
        "pitches": pitches,
        "rev": payload_version(codoc_dir),
    }
