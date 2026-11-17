"""Where kind-block content becomes ops (docs/model.md, Content as ops).

An agent (through ``codoc_write_block``), the loop's lift pass and the editor's
kind NodeViews all hand in *content*; this module turns it into ``block.set``
for non-Text fields and ``ingest`` for Text fields, with the caller's actor and
plan. No other module emits ops against a kind block, so every word in one is
attributed by ``ingest``.

``plan_write`` is pure (content → what to submit); ``write_block`` runs it
against a workspace. A second process (the MCP server) runs ``plan_write``
against a read-only replay and submits through the inbox. A new kind block is
``ingest.block_bodies``: its add and its text in one batch.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from codoc.calculus import ops as K
from codoc.calculus.doc import Block
from codoc.calculus.ops import Op
from codoc.calculus.text import Text
from codoc.kinds import get
from codoc.workspace import Workspace


@dataclass
class WritePlan:
    sets: list[dict] = field(default_factory=list)                 # block.set bodies
    ingests: list[tuple[str, str, str]] = field(default_factory=list)   # (field, base_text, new_md)
    refused: str | None = None


def plan_write(block: Block | None, content: dict) -> WritePlan:
    """What bringing ``block`` to ``content`` takes; fields not named are left
    alone. Text fields arrive as Markdown."""
    if block is None:
        return WritePlan(refused="no_block")
    if not block.live:
        return WritePlan(refused="dead_block")
    kind = get(block.kind)
    if kind is None:
        return WritePlan(refused=f"unknown kind {block.kind!r}")
    err = kind.validate(content, partial=True)
    if err:
        return WritePlan(refused=err)
    out = WritePlan()
    for k, v in content.items():
        if k in block.layout.text:
            cur = block.content.get(k)
            out.ingests.append((k, cur.live_text() if isinstance(cur, Text) else "", v))
        elif block.content.get(k) != v:
            out.sets.append({"kind": K.BLOCK_SET, "target": {"bid": block.id, "path": k}, "payload": {"value": v}})
    return out


@dataclass
class Written:
    bid: str
    ops: list[Op] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)   # (field, reason)

    @property
    def ok(self) -> bool:
        return not self.refused and all(o.conflict is None for o in self.ops)


def write_block(ws: Workspace, bid: str, content: dict, *, actor: str, plan: str | None = None,
                caused_by: str | None = None, batch: str | None = None, derived: bool = False) -> Written:
    out = Written(bid)
    p = plan_write(ws.store.blocks.get(bid), content)
    if p.refused:
        out.refused.append(("", p.refused))
        return out
    if p.sets:
        out.ops.extend(ws.submit(p.sets, actor=actor, plan=plan, caused_by=caused_by, batch=batch))
    for f, base, new_md in p.ingests:
        res, ops = ws.ingest(bid, f, base_text=base, new_md=new_md, actor=actor, plan=plan, caused_by=caused_by,
                             batch=batch, derived=derived)
        if res.refused:
            out.refused.append((f, res.refused))
        out.ops.extend(ops)
    return out
