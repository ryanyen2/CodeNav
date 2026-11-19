"""What another process reads: the queue and the state of the tree, one
feature's history, the authoring language. The CLI and the MCP tools share
these. Each reads the workspace ``client.read`` gives: this process's own when
it applies, else the logs replayed read-only.
"""
from __future__ import annotations

from pathlib import Path

from codoc.calculus import ops as K
from codoc.calculus.derive import (
    attribution, dead_cites, history as feature_history, offered_plans, phase, queued, title,
)
from codoc.daemon.client import read
from codoc.daemon.publish import realizing_is_fresh, store_state
from codoc.doclang import workspace_doc_language
from codoc.workspace.epoch import read_activity


def doc_language_block(codoc_dir: str | Path) -> dict:
    """The language the tree is authored in, as a reader is told it."""
    lang = workspace_doc_language(codoc_dir)
    block = {"code": lang.code, "name": lang.name}
    if not lang.is_default:
        block["instruction"] = (f"This tree is authored in {lang.name}. Write every title, paragraph and note in "
                                f"{lang.name}; never translate identifiers, paths or codoc: targets.")
    return block


def queue_status(codoc_dir: str | Path) -> dict:
    """Where the tree stands for its builder, all of it from one replay of
    the logs on disk; only the realizing lease, which the store does not hold,
    is read from ``status.json``. ``queued_list`` is what is left to build;
    ``reported_list`` is what this turn's calls cited (``epoch.note_cited``),
    which settles when the turn ends. State ``reported`` says every queued
    hand-off is reported."""
    store = read(codoc_dir).store
    heads = store.headings()
    phases = {h.id: phase(store, h.id) for h in heads}
    cited = set(read_activity(codoc_dir).get("cited") or [])
    items: dict[bool, list] = {False: [], True: []}
    for f in queued(store):
        did = (store.watermarks(f).handoffs or [""])[-1]
        items[did in cited].append({"feature_id": f, "title": title(store, f, "rejected"), "handoff": f"d:{did}"})
    todo, reported = items[False], items[True]
    state = store_state(store, phases)[0]
    if realizing_is_fresh(codoc_dir):
        state = "realizing"         # another pass, not the one this agent belongs to, holds the lease
    elif state == "queued" and not todo:
        state = "reported"
    dead = [{"block": bid, "symbol": m.attrs.get("symbol")} for bid, m in dead_cites(store)]
    edited = [f for f, p in phases.items() if p == "edited"]
    return {"ok": True, "state": state, "features": len(heads), "queued": len(todo), "queued_list": todo[:20],
            "reported": len(reported), "reported_list": reported[:20],
            "edited": len(edited), "edited_list": [{"feature_id": f, "title": title(store, f)} for f in edited[:20]],
            "plans": len(offered_plans(store)),
            "dead_cites": len(dead), "dead_cite_list": dead[:20],
            "doc_language": doc_language_block(codoc_dir)}


def read_history(codoc_dir: str | Path, feature_id: str, limit: int = 20) -> dict:
    """One feature's last ``limit`` ops and who wrote each span of its body.
    An op carries what it touched: ``text``, the words it inserted or removed
    or a hand-off's ``asked``; ``symbol``, the code a binding op names;
    ``mark``, a mark's kind, on or off, and what it points at. ``caused_by`` is
    the hand-off it answers. Each attribution run carries its words."""
    store = read(codoc_dir).store
    if feature_id not in store.blocks:
        return {"ok": False, "error": f"no feature {feature_id!r}"}
    entries = []
    for o in feature_history(store, feature_id)[-limit:]:
        e = {"op": o.id, "at": o.at, "actor": o.actor, "kind": o.kind, "batch": o.batch}
        if o.caused_by:
            e["caused_by"] = o.caused_by
        if o.plan:
            e["plan"] = o.plan
        if o.kind == K.TEXT_INSERT or (o.kind == K.FEATURE_HANDOFF and o.payload.get("asked")):
            e["text"] = o.payload.get("text") or o.payload.get("asked")
        if o.kind == K.TEXT_DELETE:
            # Each segment is (minting op, first character, length).
            e["text"] = "".join(str(getattr(store.by_id.get(m), "payload", {}).get("text") or "")[k:k + n]
                                for m, k, n in o.payload.get("segments") or [])
        if o.kind.startswith("bind."):
            e["symbol"] = o.payload.get("symbol")
        if o.kind == K.TEXT_MARK:
            attrs = o.payload.get("attrs") or {}
            on = "on" if o.payload.get("on", True) else "off"
            e["mark"] = " ".join(x for x in (o.payload.get("kind"), on, attrs.get("symbol") or attrs.get("href")) if x)
        entries.append(e)
    blame = {}
    for b in store.body(feature_id):
        for f in b.layout.text:
            live = b.text(f).live_text()
            blame[b.id if f == "text" else f"{b.id}.{f}"] = [
                {"actor": r.actor, "start": r.start, "end": r.end, "text": live[r.start:r.end]}
                for r in attribution(b.text(f))]
    return {"ok": True, "feature_id": feature_id, "title": title(store, feature_id), "ops": entries, "attribution": blame}
