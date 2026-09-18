"""Document → code (was ``loop_b.py``, 1,979 lines; the ledger's rows 7–8, 12).

There is no edit inference here. A person's edits are already ops on the
trunk. What this module does is small:

- ``handoff``: record ``feature.handoff`` for a feature, with the frontier it
  drains, the commit the work starts from, the person's own words, and
  whether a deletion may remove code. This is the one gesture that turns
  documentation into a request (I7).
- ``queued``: which features have handed-off work not yet realized, including
  deleted headings whose removal is still pending (phantoms).
- ``write_queue``: render every queued directive into ``realize.md``, the
  prompt the realizing session or ``codoc realize`` reads. Regenerable; the
  log is the truth.
- ``settle``: after a reflect pass ran under a closed agent epoch and cited a
  hand-off in ``caused_by``, record ``feature.realized`` for each feature
  whose bindings that pass touched. The daemon emits this, never the agent; the
  owner may also write it by hand (``codoc done``) for a request whose
  implementation moved no bound symbol.
"""
from __future__ import annotations

from pathlib import Path

from codoc.agent.base import format_prompt, load_prompt
from codoc.calculus import ops as K
from codoc.calculus.derive import directive
from codoc.calculus.ops import Op, covered
from codoc.calculus.store import Store
from codoc.loop.gitref import head_sha
from codoc.substrate.workspace import Workspace

QUEUE_FILE = "realize.md"


def handoff(ws: Workspace, fid: str, *, actor: str, asked: str = "", root_dir: str | None = None,
            delete_code: bool = False) -> Op:
    sha = head_sha(root_dir) if root_dir else ""
    return ws.submit([{"kind": K.FEATURE_HANDOFF, "target": {"fid": fid},
                       "payload": {"covers": dict(ws.store.frontier), "base_sha": sha, "asked": asked,
                                   "delete_code": delete_code}}], actor=actor)[0]


def withdraw(ws: Workspace, fid: str, *, actor: str) -> Op | None:
    """Take back the latest hand-off (its recorded inverse), as the person's op."""
    w = ws.store.watermarks_by_fid.get(fid)
    if not w or not w.handoffs:
        return None
    last = ws.store.by_id.get(w.handoffs[-1])
    if last is None or not last.inverse:
        return None
    return ws.submit([dict(b) for b in last.inverse], actor=actor)[0]


def queued(store: Store) -> list[str]:
    """Features whose hand-off frontier is not covered by their realized
    frontier, in document order; dead headings with a pending removal come
    last. A watermark fact: a feature being edited or built right now is
    still queued (the agent is reading its directive)."""
    pending = {fid for fid, w in store.watermarks_by_fid.items() if w.handoff and not covered(w.handoff, w.realized)}
    out = [h.id for h in store.headings() if h.id in pending]
    out.extend(fid for fid in pending if fid in store.blocks and not store.blocks[fid].live)
    return out


def render_queue(store: Store, root_dir: str = ".", doc_language=None) -> str:
    items = []
    for fid in queued(store):
        w = store.watermarks(fid)
        did = w.handoffs[-1] if w.handoffs else ""
        body = directive(store, fid)
        items.append(body.replace("### ", f"### ⟨d:{did}⟩ ", 1) if did else body)
    if not items:
        return ""
    template = load_prompt("realize", doc_language=doc_language)
    return format_prompt(template, root_dir=root_dir, directives="\n\n".join(items))


def write_queue(codoc_dir: str | Path, store: Store, root_dir: str = ".", doc_language=None) -> Path | None:
    path = Path(codoc_dir) / QUEUE_FILE
    text = render_queue(store, root_dir, doc_language)
    if not text:
        if path.exists():
            path.unlink()
        return None
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def settle(ws: Workspace, res, *, sha: str = "", files: list[str] | None = None, summary: str = "") -> list[Op]:
    """Record ``feature.realized`` for each feature whose latest hand-off the
    reflect pass ``res`` cited and whose bindings one of its ops touched."""
    out = []
    for fid, did in res.caused.items():
        w = ws.store.watermarks_by_fid.get(fid)
        if not w or not w.handoffs or w.handoffs[-1] != did:
            continue
        if not any(o.caused_by == did and o.kind.startswith("bind.") and o.conflict is None for o in res.ops):
            continue
        out.extend(ws.submit([{"kind": K.FEATURE_REALIZED, "target": {"fid": fid},
                               "payload": {"covers": dict(w.handoff), "sha": sha, "files": files or [],
                                           "summary": summary}}], actor="loop", caused_by=did))
    return out
