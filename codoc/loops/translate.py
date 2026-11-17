"""Rewrite an existing tree in another authoring language (``codoc translate``).

Setting ``doc_language`` changes the language codoc writes in from then on. It
never touches prose already in the tree; this is the conversion the author asks
for.

Per feature:

1. Send the title and the description blocks a person reads as prose
   (paragraphs, list items, tables; not diagrams or code) to the model, keyed by
   block id.
2. Check the answer:
   - every block came back, as the same kind of block;
   - the title does not collide with a sibling's;
   - the prose is in the target language.
3. Compute every block's ``ingest`` with ``actor=translator:<lang>``.
   ``ingest`` refuses a block whose translation drops a citation, link or bold
   span the person placed.
4. Write all of them, or none:
   - on the trunk when machines wrote the feature;
   - as one plan when a person wrote any of it, or the feature has pending
     intent, so the person's words are never replaced without a look.

A feature with an open plan is skipped, so a re-run never stacks a second
translation. Features are picked by their detected language, so an
interrupted run can be run again.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from codoc.calculus import block_md
from codoc.calculus.derive import HELD, open_plans_on, phase, title
from codoc.calculus.doc import PROSE, TABLE, parent_of, text_fields
from codoc.doclang import DocLanguage, detect_prose_language, norm_key, prose_letters, workspace_doc_language
from codoc.llm.passes import propose_translations
from codoc.loops.context import description_blocks, written_by
from codoc.workspace import Workspace
from codoc.workspace.layout import TRANSLATE, atomic_write

BATCH = 12
PROSE_BLOCKS = PROSE | {TABLE}


@dataclass
class Skipped:
    feature_id: str
    title: str
    reason: str


@dataclass
class TranslateResult:
    considered: int = 0
    already: int = 0
    translated: int = 0
    planned: int = 0
    calls: int = 0
    skipped: list[Skipped] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.translated} translated"]
        if self.planned:
            parts.append(f"{self.planned} proposed (human-written)")
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        parts.append(f"{self.already} already in the language")
        return ", ".join(parts)


def _item(ws: Workspace, fid: str) -> dict:
    """What the model is sent for one feature."""
    return {"id": fid, "title": title(ws.store, fid, "rejected"),
            "blocks": description_blocks(ws.store, fid, PROSE_BLOCKS)}


def _prose(item: dict) -> str:
    return "\n".join([item["title"], *(b["text"] for b in item["blocks"])])


def translate_tree(ws: Workspace, codoc_dir: str | Path, *, language: DocLanguage | None = None, dry_run: bool = False,
                   limit: int = 0, repo_name: str = "codebase", config=None, propose=propose_translations,
                   printer=None) -> TranslateResult:
    say = printer or (lambda *_a, **_k: None)
    lang = language or workspace_doc_language(codoc_dir)
    actor = f"translator:{lang.code}"
    res = TranslateResult()
    store = ws.store
    heads = store.headings()
    res.considered = len(heads)
    items = {h.id: _item(ws, h.id) for h in heads}
    wanted = [fid for fid, it in items.items() if detect_prose_language(_prose(it), lang).code != lang.code]
    res.already = len(heads) - len(wanted)
    pending = []
    for fid in wanted:
        if open_plans_on(store, fid):
            res.skipped.append(Skipped(fid, items[fid]["title"], "a plan is open on it"))
        else:
            pending.append(fid)
    if limit > 0:
        pending = pending[:limit]
    taken: dict[str | None, set[str]] = {}
    for i, h in enumerate(heads):
        taken.setdefault(parent_of(heads, i), set()).add(norm_key(items[h.id]["title"]))
    remaining = list(pending)

    def progress(running: bool) -> None:
        if dry_run:
            return
        atomic_write(Path(codoc_dir) / TRANSLATE, json.dumps({
            "running": running, "target": lang.code, "target_name": lang.name,
            "total": len(pending), "translated": res.translated, "planned": res.planned,
            "skipped": [s.__dict__ for s in res.skipped], "pending": remaining, "at": time.time()}))

    progress(bool(pending))
    try:
        for start in range(0, len(pending), BATCH):
            batch = pending[start:start + BATCH]
            try:
                got = propose([items[f] for f in batch], lang, repo_name=repo_name, config=config)
                res.calls += 1
            except Exception as exc:  # noqa: BLE001 — one bad batch must not sink the run
                got = {}
                for fid in batch:
                    res.skipped.append(Skipped(fid, items[fid]["title"], f"batch failed: {exc}"))
            for fid in batch:
                if fid not in got:
                    if not any(s.feature_id == fid for s in res.skipped):
                        res.skipped.append(Skipped(fid, items[fid]["title"], "no translation returned"))
                    continue
                new_title, new_blocks = got[fid]
                reason = _check(items[fid], new_title, new_blocks, lang, taken.setdefault(_parent(heads, fid), set()))
                if reason is None and not dry_run:
                    reason = _write(ws, fid, new_title, new_blocks, actor, lang, res)
                if reason:
                    res.skipped.append(Skipped(fid, items[fid]["title"], reason))
                else:
                    res.translated += 1
            remaining = [f for f in remaining if f not in batch]
            progress(True)
            say(f"  · translated {res.translated}/{len(pending)}")
    finally:
        progress(False)
    return res


def _parent(heads, fid: str) -> str | None:
    return parent_of(heads, next(i for i, h in enumerate(heads) if h.id == fid))


def _check(item: dict, new_title: str, new_blocks: dict[str, str], lang: DocLanguage, siblings: set[str]) -> str | None:
    """Why this translation cannot be written, or None. Records the new title
    among the siblings when it passes."""
    if not new_title.strip():
        return "empty title"
    for b in item["blocks"]:
        if not (new_blocks.get(b["id"]) or "").strip():
            return f"block {b['id']} came back empty or missing"
        old, new = block_md.parse(b["text"], headings=False), block_md.parse(new_blocks[b["id"]], headings=False)
        if len(new) != 1 or new[0].kind != old[0].kind:
            return f"block {b['id']} came back as a different kind of block"
    key = norm_key(new_title)
    if key in siblings and key != norm_key(item["title"]):
        return f"the translated title collides with a sibling's ({new_title!r})"
    old_prose = "\n".join(b["text"] for b in item["blocks"])
    new_prose = "\n".join(new_blocks[b["id"]] for b in item["blocks"])
    if prose_letters(old_prose) >= 12 and (got := detect_prose_language(new_prose, lang)).code != lang.code:
        return f"came back in {got.name}, not {lang.name}"
    siblings.add(key)
    return None


def _write(ws: Workspace, fid: str, new_title: str, new_blocks: dict[str, str], actor: str, lang: DocLanguage,
           res: TranslateResult) -> str | None:
    """Compute every ingest first, then write all of them or none. Returns
    why nothing was written, or None.

    The machine gate (``must_propose``) does not apply: a translation replaces
    every word by design, so the deleted fraction says nothing. What decides
    trunk or plan is whose words they were."""
    store = ws.store
    human = written_by(store, fid) == "human" or phase(store, fid) in HELD
    targets = [(fid, "text", new_title)]
    for bid, md in new_blocks.items():
        block = store.blocks.get(bid)
        if block is None or block.kind not in PROSE_BLOCKS:
            continue
        path = text_fields(block.kind)[0]
        spec = block_md.parse(md, headings=False)[0]
        targets.append((bid, path, spec.text))
    computed = []
    for bid, path, new_md in targets:
        block = store.blocks[bid]
        r = ws.plan_ingest(bid, path, base_text=block.text(path).live_text(), new_md=new_md, actor=actor, trunk=not human)
        if r.refused:
            return r.refused if bid == fid else f"{bid}: {r.refused}"
        computed.append(r)
    base = dict(store.frontier)
    plan = ws.open_plan(actor=actor, kind="edit", target=fid, note=f"translation to {lang.name}") if human else None
    for r in computed:
        ws.commit_ingest(r, actor=actor, plan=plan, batch=plan, base=base)
    if plan:
        res.planned += 1
    return None
