"""Migrate an existing tree into another authoring language (``codoc translate``).

Switching ``doc_language`` changes what codoc originates from then on; it never
touches prose already on the page. This is the author-invoked conversion.

On the calculus it is ``ingest`` per block with ``actor=translator:<lang>``:
the translation of a machine-written block lands on the trunk (the author
asked for exactly this rewrite), the translation of a human-written block opens
a plan so the author's own words are never replaced without a look. ``ingest``
already refuses a block whose translation would drop a citation, link or focus
span (spec §4, rule 5), so this module adds only the two checks ``ingest``
does not make: did the prose actually change script, and does the title collide
with a sibling's. Blocks with an open translator plan are skipped, so a re-run
never stacks a second translation. Selection is by detected language, so an
interrupted run is safe to re-run.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from codoc.llm.passes import propose_translations
from codoc.calculus.derive import HELD, markdown, phase
from codoc.calculus.doc import PARAGRAPH
from codoc.doclang import DocLanguage, detect_prose_language, norm_key, prose_letters, workspace_doc_language
from codoc.loops.context import written_by
from codoc.workspace import Workspace
from codoc.workspace.layout import TRANSLATE, atomic_write

BATCH = 12
LEASE_S = 300


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


def _feature_text(ws: Workspace, fid: str) -> tuple[str, list[tuple[str, str]]]:
    body = ws.store.feature_body(fid)
    t = markdown(body[0].text(), "rejected", ws.store)
    paras = [(b.id, markdown(b.text(), "rejected", ws.store)) for b in body[1:] if b.kind == PARAGRAPH]
    return t, paras


def _needs(ws: Workspace, fid: str, lang: DocLanguage) -> bool:
    t, paras = _feature_text(ws, fid)
    text = t + "\n" + "\n".join(p for _, p in paras)
    return detect_prose_language(text, lang).code != lang.code


def _has_open_plan(ws: Workspace, fid: str) -> bool:
    """A feature with any open plan waits: the live text holds words the
    person has not accepted, and translating around them would tombstone
    them as the translator's own edit."""
    from codoc.calculus.derive import open_plans_on
    return bool(open_plans_on(ws.store, fid))


def translate_tree(ws: Workspace, codoc_dir: str | Path, *, language: DocLanguage | None = None, dry_run: bool = False,
                   limit: int = 0, repo_name: str = "codebase", config=None, propose=propose_translations,
                   printer=None) -> TranslateResult:
    say = printer or (lambda *_a, **_k: None)
    lang = language or workspace_doc_language(codoc_dir)
    actor = f"translator:{lang.code}"
    res = TranslateResult()
    heads = ws.store.headings()
    res.considered = len(heads)
    wanted = [h.id for h in heads if _needs(ws, h.id, lang)]
    pending = [fid for fid in wanted if not _has_open_plan(ws, fid)]
    res.already = len(heads) - len(wanted)
    for fid in wanted:
        if fid in pending:
            continue
        res.skipped.append(Skipped(fid, markdown(ws.store.blocks[fid].text(), "rejected", ws.store),
                                   "a plan is open on it; translating would tombstone its words"))
    if limit > 0:
        pending = pending[:limit]
    taken: dict[str | None, set[str]] = {}
    from codoc.calculus.doc import parent_of
    for i, h in enumerate(heads):
        taken.setdefault(parent_of(heads, i), set()).add(norm_key(markdown(h.text(), "rejected", ws.store)))
    remaining = list(pending)

    def progress(running: bool) -> None:
        if dry_run:
            return
        atomic_write(Path(codoc_dir) / TRANSLATE, json.dumps({
            "version": 2, "running": running, "target": lang.code, "target_name": lang.name,
            "total": len(pending), "translated": res.translated, "planned": res.planned,
            "skipped": [s.__dict__ for s in res.skipped], "pending": remaining, "at": time.time()}))

    progress(bool(pending))
    try:
        for start in range(0, len(pending), BATCH):
            batch = pending[start:start + BATCH]
            payload = []
            paras_by_fid: dict[str, list[tuple[str, str]]] = {}
            for fid in batch:
                t, paras = _feature_text(ws, fid)
                paras_by_fid[fid] = paras
                payload.append({"id": fid, "title": t, "description": "\n\n".join(p for _, p in paras)})
            try:
                got = propose(payload, lang, repo_name=repo_name, config=config)
                res.calls += 1
            except Exception as exc:  # noqa: BLE001 — one bad batch must not sink the run
                for fid in batch:
                    res.skipped.append(Skipped(fid, next(p["title"] for p in payload if p["id"] == fid),
                                               f"batch failed: {exc}"))
                got = {}
            for fid in batch:
                t_old = next(p["title"] for p in payload if p["id"] == fid)
                pair = got.get(fid)
                if pair is None:
                    res.skipped.append(Skipped(fid, t_old, "no translation returned"))
                    continue
                new_title, new_desc = pair
                reason = _check(ws, fid, t_old, payload, new_title, new_desc, lang, taken, heads)
                if reason:
                    res.skipped.append(Skipped(fid, t_old, reason))
                    continue
                if dry_run:
                    res.translated += 1
                    continue
                new_paras = [p.strip() for p in new_desc.split("\n\n") if p.strip()]
                old_paras = paras_by_fid[fid]
                if len(new_paras) != len(old_paras):
                    res.skipped.append(Skipped(fid, t_old, "paragraph count changed"))
                    continue
                # Every refusal is known before the plan exists. A plan opened
                # and then abandoned is a verdict the editor offers on nothing,
                # and a translator has no authority to withdraw one.
                # Not `must_propose`: a translation replaces every word by
                # design, so the fraction says nothing, and the question is
                # whose words they were. The one clause it shares with the
                # gate is 01 §4 rule 8 — while a feature has pending intent the
                # document wins, whoever wrote it.
                human = written_by(ws.store, fid) == "human" or phase(ws.store, fid) in HELD
                base = dict(ws.store.frontier)
                computed, refused = [], None
                for bid, new_md in [(fid, new_title)] + [(b, md) for (b, _o), md in zip(old_paras, new_paras)]:
                    r = ws.plan_ingest(bid, "text", base_text=ws.store.blocks[bid].text().live_text(),
                                       new_md=new_md, actor=actor, trunk=not human)
                    if r.refused:
                        refused = r.refused if bid == fid else f"{bid}: {r.refused}"
                        break
                    computed.append(r)
                if refused:
                    res.skipped.append(Skipped(fid, t_old, refused))
                    continue
                plan = ws.open_plan(actor=actor, kind="edit", target=fid, note=f"translation to {lang.name}") if human else None
                for r in computed:
                    ws.commit_ingest(r, actor=actor, plan=plan, batch=plan, base=base)
                res.translated += 1
                if plan:
                    res.planned += 1
            remaining = [f for f in remaining if f not in batch]
            progress(True)
            say(f"  · translated {res.translated}/{len(pending)}")
    finally:
        progress(False)
    return res


def _check(ws, fid, old_title, payload, new_title, new_desc, lang, taken, heads) -> str | None:
    if not new_title.strip():
        return "empty title"
    old_desc = next(p["description"] for p in payload if p["id"] == fid)
    if old_desc.strip() and not new_desc.strip():
        return "empty description"
    from codoc.calculus.doc import parent_of
    idx = next(i for i, h in enumerate(heads) if h.id == fid)
    parent = parent_of(heads, idx)
    key = norm_key(new_title)
    if key in taken.get(parent, set()) and key != norm_key(old_title):
        return f"the translated title collides with a sibling's ({new_title!r})"
    if prose_letters(old_desc) >= 12 and detect_prose_language(new_desc, lang).code != lang.code:
        return f"came back in {detect_prose_language(new_desc, lang).name}, not {lang.name}"
    taken.setdefault(parent, set()).add(key)
    return None
