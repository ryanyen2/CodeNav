"""Derived files the daemon writes after a pass (spec §6; `06-cut-list.md` §6).

    tree.codoc        the Markdown export (tracked)
    projection.json   what the editor and the hub render
    realize.md        the queued directives, as the realizing agent reads them
    status.json       {state, pending, detail, head, at}

All derived, all regenerable, all written atomically. Nothing here is read
back by the calculus. ``status.json`` keeps one ephemeral input the store does
not hold: the realizing lease, which a live realize pass renews by rewriting
the file and which decays by mtime. Phases are computed once per publish and
shared by every file.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from codoc.calculus.derive import ground, ink, open_plans, phase, plan_claims, projection
from codoc.calculus.export import export_markdown
from codoc.calculus.store import Store
from codoc.loops.realize import queued, write_queue

PROJECTION_FILE = "projection.json"
STATUS_FILE = "status.json"
EXPORT_FILE = "tree.codoc"
HISTORY_FILE = "history.json"
HISTORY_WINDOW = 200
REALIZING_LEASE_SECONDS = 300


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def phases_of(store: Store, realizing: set[str] = frozenset()) -> dict[str, str]:
    return {h.id: phase(store, h.id, realizing) for h in store.headings()}


def write_projection(codoc_dir: str | Path, store: Store, phases: dict[str, str]) -> Path:
    p = projection(store, phases=phases)
    by_feature: dict[str, list[dict]] = {}
    by_file: dict[str, list[str]] = {}
    for b in store.bindings.values():
        by_feature.setdefault(b.feature, []).append({"file": b.file, "symbol": b.symbol})
        by_file.setdefault(b.file, [])
        if b.feature not in by_file[b.file]:
            by_file[b.file].append(b.feature)
    p["bindings"] = {"by_feature": by_feature, "by_file": by_file}
    p["claims"] = {}
    for fid in p["phases"]:
        claims = ink(store, fid) + ground(store, fid) + plan_claims(store, fid)
        if claims:
            p["claims"][fid] = [c.__dict__ for c in claims]
    path = Path(codoc_dir) / PROJECTION_FILE
    atomic_write(path, json.dumps(p, ensure_ascii=False))
    return path


def write_history(codoc_dir: str | Path, store: Store) -> Path:
    from codoc.calculus.window import history_window
    path = Path(codoc_dir) / HISTORY_FILE
    atomic_write(path, json.dumps(history_window(store, HISTORY_WINDOW), ensure_ascii=False))
    return path


def write_export(codoc_dir: str | Path, store: Store) -> Path:
    path = Path(codoc_dir) / EXPORT_FILE
    text = export_markdown(store)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        atomic_write(path, text)
    return path


def realizing_is_fresh(codoc_dir: str | Path, now: float | None = None) -> bool:
    path = Path(codoc_dir) / STATUS_FILE
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if d.get("state") != "realizing":
            return False
        age = (now or time.time()) - path.stat().st_mtime
        return age < REALIZING_LEASE_SECONDS and (Path(codoc_dir) / "realize.md").exists()
    except (OSError, json.JSONDecodeError):
        return False


def write_status(codoc_dir: str | Path, store: Store, *, phases: dict[str, str] | None = None,
                 realizing: bool | None = None, detail: str = "") -> dict:
    """Derive the workspace state and write it. A fresh realizing lease is
    preserved untouched (a rewrite would renew the lease it just checked)."""
    path = Path(codoc_dir) / STATUS_FILE
    if realizing is None:
        if realizing_is_fresh(codoc_dir):
            return json.loads(path.read_text(encoding="utf-8"))
        realizing = False
    phases = phases if phases is not None else phases_of(store)
    q = queued(store)
    edited = sum(1 for p in phases.values() if p == "edited")
    proposals = len(open_plans(store))
    if realizing:
        state, pending = "realizing", len(q)
    elif q:
        state, pending = "queued", len(q)
        detail = detail or f"{len(q)} change(s) ready to implement — run /codoc:sync or codoc realize"
    elif edited:
        state, pending = "edited", edited
        detail = detail or f"{edited} feature(s) with unsent edits — hand off to build"
    elif proposals:
        state, pending = "proposed", proposals
        detail = detail or f"{proposals} proposal(s) awaiting your verdict"
    else:
        state, pending = "synced", len(phases)
    d = {"version": 2, "state": state, "pending": pending, "detail": detail, "head": store.pos,
         "features": len(phases), "at": time.time()}
    atomic_write(path, json.dumps(d))
    return d


def publish(codoc_dir: str | Path, store: Store, *, root_dir: str = ".", realizing: set[str] = frozenset(),
            doc_language=None) -> dict:
    phases = phases_of(store, realizing)
    write_projection(codoc_dir, store, phases)
    write_history(codoc_dir, store)
    write_export(codoc_dir, store)
    write_queue(codoc_dir, store, root_dir, doc_language)
    return write_status(codoc_dir, store, phases=phases)
