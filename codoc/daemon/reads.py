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


def read_history(codoc_dir: str | Path, feature_id: str, limit: int = 20, *, ops: bool = False) -> dict:
    """One feature's last ``limit`` changes, in the order they happened, and
    who wrote each span of its body.

    A change is what a reader means by one: a gesture, a reflect pass, an
    amend, a plan's opening, a verdict. The writers give each one batch, so a
    row is a run of consecutive ops of one batch in the log, except that a
    hand-off, a verdict or a realization is a row of its own. Each row says
    when, who, the plan its ops carry, the hand-off they answer
    (``caused_by``) and a one-line ``summary`` of what they did. With ``ops`` a
    row also lists its ops, each with what it touched: ``text``, the words
    inserted or removed or a hand-off's ``asked``; ``symbol``, the code a
    binding names; ``mark``, a mark's kind, on or off, and what it points at.
    Each attribution run carries its words."""
    store = read(codoc_dir).store
    if feature_id not in store.blocks:
        return {"ok": False, "error": f"no feature {feature_id!r}"}
    groups: list[list] = []
    for o in feature_history(store, feature_id):
        if (o.kind in _GESTURES or not groups or groups[-1][-1].kind in _GESTURES
                or (o.batch or o.id) != (groups[-1][-1].batch or groups[-1][-1].id)):
            groups.append([])
        groups[-1].append(o)
    rows = []
    for group in groups[-limit:]:
        batch = group[0].batch or group[0].id
        entries = [_op_entry(store, o) for o in group]
        row = {"batch": batch, "at": group[0].at, "actor": ", ".join(dict.fromkeys(o.actor for o in group)),
               "summary": _summary(entries)}
        plans = {o.plan for o in group if o.plan}
        causes = {o.caused_by for o in group if o.caused_by}
        if plans:
            row["plan"] = ", ".join(sorted(plans))
        if causes:
            row["caused_by"] = ", ".join(sorted(causes))
        if ops:
            row["ops"] = entries
        rows.append(row)
    blame = {}
    for b in store.body(feature_id):
        for f in b.layout.text:
            live = b.text(f).live_text()
            blame[b.id if f == "text" else f"{b.id}.{f}"] = [
                {"actor": r.actor, "start": r.start, "end": r.end, "text": live[r.start:r.end]}
                for r in attribution(b.text(f))]
    return {"ok": True, "feature_id": feature_id, "title": title(store, feature_id), "batches": rows, "attribution": blame}


def _op_entry(store, o) -> dict:
    e = {"op": o.id, "at": o.at, "actor": o.actor, "kind": o.kind}
    if o.conflict:
        e["conflict"] = o.conflict
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
    if o.kind == K.BLOCK_ADD:
        e["block"] = o.payload.get("kind")
    if o.kind in (K.PLAN_OPEN, K.PLAN_ACCEPT, K.PLAN_REJECT, K.PLAN_REOPEN):
        e["plan"] = o.target.get("pid")
    return e


# Ops that are a change on their own, whatever batch they share.
_GESTURES = frozenset({K.FEATURE_HANDOFF, K.FEATURE_REALIZED, K.PLAN_ACCEPT, K.PLAN_REJECT, K.PLAN_REOPEN})
# A plan.open is tagged with its own plan, which the row already names.
_VERBS = {K.PLAN_OPEN: "opened", K.PLAN_ACCEPT: "accepted plan", K.PLAN_REJECT: "rejected plan",
          K.PLAN_REOPEN: "reopened plan"}
_BLOCKS = {"numbered": "numbered item", "code": "code block"}       # any other kind reads as its name
_COUNTED = {K.BLOCK_SET: ("block reformatted", "blocks reformatted"), K.BLOCK_SPLIT: ("split", "splits"),
            K.BLOCK_MERGE: ("merge", "merges"), K.BLOCK_MOVE: ("move", "moves"),
            K.BLOCK_DELETE: ("block deleted", "blocks deleted"), K.BLOCK_RESTORE: ("block restored", "blocks restored"),
            K.TEXT_RESTORE: ("restore", "restores")}


def _summary(entries: list[dict]) -> str:
    """What one batch did, as a reader would say it: the gesture first (a
    hand-off, a verdict, a realization), then blocks added, words added and
    removed, other structure, bindings and marks. Conflicted ops did nothing
    and are left out of the counts."""
    live = [e for e in entries if not e.get("conflict")]
    parts: list[str] = []
    for e in live:
        if e["kind"] == K.FEATURE_HANDOFF:
            parts.append(f"hand-off: {e['text']!r}" if e.get("text") else "hand-off")
        elif e["kind"] == K.FEATURE_REALIZED:
            parts.append("realized")
        elif e["kind"] == K.PLAN_OPEN:
            parts.append(_VERBS[K.PLAN_OPEN])
        elif e["kind"] in _VERBS:
            parts.append(f"{_VERBS[e['kind']]} {e.get('plan') or ''}".rstrip())
    added: dict[str, int] = {}
    for e in live:
        if e["kind"] == K.BLOCK_ADD:
            noun = _BLOCKS.get(e.get("block") or "", e.get("block") or "block")
            added[noun] = added.get(noun, 0) + 1
    if added:
        parts.append(", ".join(f"{n} {noun}{'s' if n > 1 else ''}" for noun, n in added.items()) + " added")
    for kind, sign in ((K.TEXT_INSERT, "added"), (K.TEXT_DELETE, "removed")):
        n = sum(len((e.get("text") or "").split()) for e in live if e["kind"] == kind)
        if n:
            parts.append(f"{n} word{'s' if n > 1 else ''} {sign}")
    for kind, (one, many) in _COUNTED.items():
        n = sum(1 for e in live if e["kind"] == kind)
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    for kind, verb in ((K.BIND_ATTACH, "bound"), (K.BIND_DETACH, "unbound"), (K.BIND_REFRESH, "refreshed")):
        symbols = [e["symbol"] for e in live if e["kind"] == kind and e.get("symbol")]
        if symbols:
            parts.append(f"{verb} {symbols[0]}" + (f" and {len(symbols) - 1} more" if len(symbols) > 1 else ""))
    marks = [e["mark"] for e in live if e["kind"] == K.TEXT_MARK and e.get("mark")]
    if marks:
        parts.append(", ".join(dict.fromkeys(marks)))
    if not parts and len(live) < len(entries):
        parts.append("refused: " + next(e["conflict"] for e in entries if e.get("conflict")))
    return "; ".join(parts) or ", ".join(dict.fromkeys(e["kind"] for e in entries))
