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
_ACTIVITY_FILENAME = "activity.json"
# A wall-clock-ms HLC leaves ample room below this shift for the logical counter.
_LOGICAL_BITS = 20


def _sidecar(codoc_dir: str | Path) -> dict:
    return read_json(Path(codoc_dir) / BINDINGS_FILENAME, default={}) or {}


def _status(codoc_dir: str | Path) -> dict:
    return read_json(Path(codoc_dir) / STATUS_FILENAME, default={}) or {}


_TREE_FILENAME = "tree.codoc"


_DB_FILENAME = "codoc.db"


def _doc(codoc_dir: str | Path):
    """The store projection (store is the single source of truth, R3) when the store
    exists; else the webview-authored rich doc; else — when both are absent/empty
    because the workspace was never opened in VS Code — one rendered from
    ``tree.codoc`` so the hub is self-sufficient (it has no editor to author the doc)."""
    codoc_dir = Path(codoc_dir)
    if (codoc_dir / _DB_FILENAME).is_file():
        from codoc.codoc_file.doc_render import build_doc_from_store
        from codoc.store.db import open_store

        with open_store(codoc_dir) as store:
            return build_doc_from_store(store)
    doc = read_json(codoc_dir / _DOC_FILENAME, default=None)
    if isinstance(doc, dict) and (doc.get("content") or []):
        return doc
    tree_path = codoc_dir / _TREE_FILENAME
    if tree_path.is_file():
        text = tree_path.read_text(encoding="utf-8")
        if text.strip():
            from codoc.codoc_file.doc_render import build_doc_from_text

            return build_doc_from_text(text)
    return doc


def _activity(codoc_dir: str | Path) -> dict:
    return read_json(Path(codoc_dir) / _ACTIVITY_FILENAME, default={}) or {}


_MAX_STEPS = 5
_TOOL_VERB = {
    "Edit": "editing", "Write": "editing", "MultiEdit": "editing", "NotebookEdit": "editing",
    "Read": "reading", "Bash": "running", "Grep": "searching", "Glob": "searching",
}


def _phases_from_activity(activity: dict, *, now: float | None = None) -> dict:
    """Per-feature reflection phase (editing/reflecting/done) — the browser-path parity of
    the TS ``featurePhases``. Drives the heading dot + the ghost→resolved reveal on the hub.

    TTL-filtered on each entry's own ``at`` timestamp (`FEATURE_PHASE_TTL_SECONDS`):
    only the ``Stop`` hook clears a feature's phase, and it never fires on an
    interrupted/killed session, so an un-filtered read would show "editing" forever."""
    import time as _time
    from datetime import datetime

    from codoc.loop.activity import FEATURE_PHASE_TTL_SECONDS

    if now is None:
        now = _time.time()
    out: dict[str, str] = {}
    for fid, entry in (activity.get("features") or {}).items():
        if not (isinstance(entry, dict) and entry.get("phase")):
            continue
        at = entry.get("at")
        if at:
            try:
                ts = datetime.fromisoformat(at).timestamp()
                if (now - ts) > FEATURE_PHASE_TTL_SECONDS:
                    continue  # stale — an interrupted session left this set forever
            except (ValueError, TypeError):
                pass
        out[fid] = entry["phase"]
    return out


def _steps_from_activity(activity: dict, sidecar: dict, *, alive: bool = True) -> dict:
    """Per-feature agent-action steps for the ribbon (parity of TS ``featureSteps``).
    Prefers the ``recent`` event log, falling back to ``touched``; resolves a file to its
    features via the sidecar ``by_file`` index. Last step active, earlier ones done.

    ``alive`` is the caller's lease-checked liveness (`codoc.loop.activity.epoch_alive`),
    not the raw ``epoch.open`` flag — a hard-killed session never fires the ``Stop`` hook
    that would otherwise close the epoch, so trusting the flag directly would show the
    ribbon "active" long after the agent is gone."""
    if not alive:
        return {}

    by_file = sidecar.get("by_file") or {}

    def fids_for(file: str, explicit: list) -> set[str]:
        s = set(explicit or [])
        for e in by_file.get(file) or []:
            if isinstance(e, dict) and e.get("feature_id"):
                s.add(e["feature_id"])
        return s

    def base(p: str) -> str:
        return p.rsplit("/", 1)[-1] if p else p

    labels_by_fid: dict[str, list[str]] = {}
    recent = activity.get("recent") or []
    if recent:
        for r in recent:
            if not isinstance(r, dict):
                continue
            label = f"{_TOOL_VERB.get(r.get('tool', ''), (r.get('tool') or '').lower())} {base(r.get('file', ''))}".strip()
            for fid in fids_for(r.get("file", ""), r.get("feature_ids") or []):
                lst = labels_by_fid.setdefault(fid, [])
                if not lst or lst[-1] != label:
                    lst.append(label)
    else:
        for file, entry in (activity.get("touched") or {}).items():
            if not isinstance(entry, dict):
                continue
            verb = "editing" if entry.get("mode") == "write" else "reading"
            label = f"{verb} {base(file)}"
            for fid in fids_for(file, entry.get("feature_ids") or []):
                lst = labels_by_fid.setdefault(fid, [])
                if label not in lst:
                    lst.append(label)

    out: dict[str, list[dict]] = {}
    for fid, labels in labels_by_fid.items():
        trimmed = labels[-_MAX_STEPS:]
        out[fid] = [{"label": lab, "done": i < len(trimmed) - 1} for i, lab in enumerate(trimmed)]
    return out


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


#: Inline-line collapse cap — mirrors protocol.ts THREADS_COLLAPSE_AT.
_THREADS_COLLAPSE_AT = 5


def _threads_from_sidecar(sidecar: dict) -> dict:
    """Per-feature Connections strands (reads / usedBy / refs), the browser-payload
    parity of the VS Code ``directedEdges`` + ``assembleThreads`` (``src/state/``).

    Powers the inline threads line AND the dependency-flow panel. ``reads`` =
    out-edges (this feature depends on →), ``usedBy`` = in-edges (← used by); both
    rank by coupling ``weight`` (desc, tie-broken by title) and dedup within their
    strand. ``refs`` are the bound code chunks (ranked file then symbol). The
    ``consult`` strand is parsed from feature DESCRIPTIONS in the VS Code host; the
    sidecar carries none, so it is empty here (the hub is a suggest surface — the
    flow panel and inline deps line do not depend on it)."""
    features = sidecar.get("features") or {}
    by_feature = sidecar.get("by_feature") or {}
    feature_edges = sidecar.get("feature_edges") or {}

    def title_of(fid: str) -> str:
        meta = features.get(fid)
        return (meta.get("title") or "") if isinstance(meta, dict) else ""

    # Directed out/in maps from feature_edges (drop self-loops), mirroring directedEdges.
    out: dict[str, list[dict]] = {}
    inn: dict[str, list[dict]] = {}
    for src, edges in feature_edges.items():
        for e in edges or []:
            to = e.get("to")
            if not to or to == src:
                continue
            edge = {"weight": e.get("weight", 0), "kinds": e.get("kinds") or []}
            out.setdefault(src, []).append({"to": to, **edge})
            inn.setdefault(to, []).append({"to": src, **edge})

    def targets(edges: list[dict], self_id: str) -> list[dict]:
        seen: set[str] = set()
        rows: list[dict] = []
        for e in edges:
            to = e["to"]
            t = title_of(to)
            if not t or to == self_id or to in seen:
                continue
            seen.add(to)
            rows.append({"toId": to, "toTitle": t, "weight": e.get("weight", 0), "kinds": e.get("kinds") or []})
        rows.sort(key=lambda r: (-(r["weight"] or 0), r["toTitle"].lower()))
        return rows

    threads: dict[str, dict] = {}
    for fid in features:
        if not isinstance(features.get(fid), dict):
            continue
        reads = targets(out.get(fid, []), fid)
        used_by = targets(inn.get(fid, []), fid)
        ref_seen: set[str] = set()
        refs = []
        for b in by_feature.get(fid) or []:
            if not isinstance(b, dict):
                continue
            key = f"{b.get('file', '')} {b.get('symbol', '')}"
            if key in ref_seen:
                continue
            ref_seen.add(key)
            refs.append({"file": b.get("file", ""), "symbol": b.get("symbol", "")})
        refs.sort(key=lambda r: (r["file"], r["symbol"]))
        if not reads and not used_by and not refs:
            continue
        threads[fid] = {
            "reads": reads,
            "usedBy": used_by,
            "refs": refs,
            "consult": [],
            "collapsed": {
                "reads": len(reads) > _THREADS_COLLAPSE_AT,
                "usedBy": len(used_by) > _THREADS_COLLAPSE_AT,
                "refs": len(refs) > _THREADS_COLLAPSE_AT,
                "consult": False,
            },
        }
    return threads


def _blocks_with_media(blocks: dict) -> dict:
    """Attach a `mediaSrc` to each `image` block entry the browser can load
    directly (`/api/media/<name>` or a pass-through `http(s)://` ref) — mirrors
    the webview's `asWebviewUri` translation so both hosts render the same
    attachment without either resolving a raw filesystem path themselves."""
    from codoc.serve.media import media_url_for

    out: dict[str, list[dict]] = {}
    for fid, entries in blocks.items():
        out[fid] = [
            {**e, "mediaSrc": media_url_for(e.get("content", ""))} if e.get("kind") == "image" else e
            for e in (entries or [])
        ]
    return out


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
    activity = _activity(codoc_dir)
    from codoc.loop.activity import epoch_alive

    return {
        "nodes": nodes,
        "roots": roots,
        "status": {"state": state, "pending": pending},
        "sync": {
            "state": state,
            "pending": pending,
            "activeWrite": [],
            "activeRead": [],
            "phase": _phases_from_activity(activity),
            "steps": _steps_from_activity(activity, sidecar, alive=epoch_alive(codoc_dir)),
        },
        "rootName": codoc_dir.parent.name,
        "pendingEventIds": [],
        "doc": _doc(codoc_dir),
        "threads": _threads_from_sidecar(sidecar),
        "awaitingAI": holds,
        "holdDetail": sidecar.get("hold_detail") or {},
        "drafts": drafts,
        "pitches": pitches,
        # v6: typed-media blocks per feature. Re-shaped straight from the sidecar
        # slice, no re-derivation — the hub is a file-channel client (KTD7). This
        # is the "many surfaces" evidence: a second host rendering the same
        # blocks. Block EDITS route through dispatch.py's suggest-gated
        # "block-edit" command; an `image` block's local attachment gets a
        # `mediaSrc` the browser can load directly (mirrors the webview's
        # `asWebviewUri` translation — see codoc/serve/media.py).
        "blocks": _blocks_with_media(_sidecar(codoc_dir).get("blocks") or {}),
        "rev": payload_version(codoc_dir),
    }
