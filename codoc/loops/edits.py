"""Tree edits → ops.

A tree edit is what a model pass or a coding agent says about the tree, as JSON:

    attach       {feature_id, bindings}
    detach       {feature_id, bindings}
    add_node     {id?, parent_id?, title, description, bindings?}
    amend        {feature_id, title?, blocks: [{id, text} | {id, delete: true} | {text}]}
    move_node    {feature_id, parent_id?}
    retire_node  {feature_id}

- ``bindings`` is a list of symbols, each ``file::symbol_path``.
- ``description`` and each ``text`` are Markdown (``calculus.block_md``).
- ``id`` on an ``add_node`` is a temporary name; a later edit in the same reply
  may use it as ``parent_id``.

``Compiler.compile`` turns one edit into a ``Change``. It reads the store and
writes nothing. Where the change lands (the trunk or a plan, as which actor,
citing which hand-off) is the caller's decision: the bootstrap, the reflect
pass and the MCP tools each apply a ``Change`` through their own writer.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from codoc.calculus import block_md
from codoc.calculus import ops as K
from codoc.calculus.block_md import BlockSpec
from codoc.calculus.doc import HEADING, ITEMS, PROSE, SOURCE
from codoc.calculus.ids import START
from codoc.calculus.ingest import block_bodies, concat_bodies
from codoc.calculus.store import Store

KINDS = ("attach", "detach", "add_node", "amend", "move_node", "retire_node")
# The plan an edit opens when it lands as a proposal; any other edit is an `edit` plan.
PLAN_KIND = {"add_node": "add", "move_node": "move", "retire_node": "delete"}


def delete_open(store: Store, fid: str) -> bool:
    """Whether ``fid`` is already proposed for deletion by an open plan."""
    return any(pl.kind == "delete" and pl.target == fid for pl in store.open_plans.values())


@dataclass
class Rewrite:
    """New text for one existing Text field: an ``ingest``."""
    bid: str
    path: str
    new_md: str


@dataclass
class Change:
    """What one edit asks for.

    ``bodies`` is one batch, to be submitted unchanged (it carries ``$ins``
    anchors). ``rewrites`` are ingests of existing fields. ``strikes`` are
    blocks proposed for removal, which only a plan can carry
    (``strike_bodies``). ``structural`` says the change adds, removes, moves or
    re-kinds a block, as opposed to only rewording text and binding code."""
    kind: str
    target: str | None = None
    note: str = ""
    bodies: list[dict] = field(default_factory=list)
    rewrites: list[Rewrite] = field(default_factory=list)
    strikes: list[str] = field(default_factory=list)
    bound: list[tuple[str, str]] = field(default_factory=list)     # (file, symbol) this change attaches
    structural: bool = False
    refused: str | None = None

    @property
    def empty(self) -> bool:
        return not (self.bodies or self.rewrites or self.strikes)


def validate(raw) -> str | None:
    """Why ``raw`` cannot be compiled, or None. Shape only: ids are checked
    against the store by ``compile``."""
    if not isinstance(raw, dict):
        return "not an object"
    kind = raw.get("kind")
    if kind not in KINDS:
        return f"unknown kind {kind!r}"
    if kind != "add_node" and not isinstance(raw.get("feature_id"), str):
        return f"{kind}.feature_id must be a string"
    if kind == "add_node" and not (isinstance(raw.get("title"), str) and raw["title"].strip()):
        return "add_node.title must be a non-empty string"
    for name in ("title", "description", "rationale", "parent_id", "id"):
        if raw.get(name) is not None and not isinstance(raw[name], str):
            return f"{kind}.{name} must be a string"
    if "bindings" in raw and not isinstance(raw["bindings"] or [], list):
        return f"{kind}.bindings must be a list"
    if kind in ("attach", "detach") and not raw.get("bindings"):
        return f"{kind}.bindings is empty"
    blocks = raw.get("blocks")
    if blocks is not None:
        if kind != "amend" or not isinstance(blocks, list) or not all(isinstance(e, dict) for e in blocks):
            return "amend.blocks must be a list of objects"
        for e in blocks:
            if e.get("id") is not None and not isinstance(e["id"], str):
                return "amend.blocks[].id must be a string"
            if e.get("text") is not None and not isinstance(e["text"], str):
                return "amend.blocks[].text must be a string"
            if e.get("id") is None and not (e.get("text") or "").strip():
                return "amend.blocks[] entry has neither id nor text"
    return None


def symbol(binding) -> tuple[str, str] | None:
    """A binding as ``(file, symbol)``. The symbol is ``file::symbol_path``; a
    ``[file, symbol]`` pair is read too, since the symbol alone says the file."""
    if isinstance(binding, (list, tuple)) and len(binding) == 2:
        binding = binding[1]
    if not isinstance(binding, str) or "::" not in binding:
        return None
    return binding.split("::", 1)[0], binding


def one_line(md: str) -> str:
    return " ".join((md or "").split())


def title_from_file(file: str) -> str:
    """A title for a file nobody has described: its stem as words
    (``rate_limit.py`` → ``Rate limit``)."""
    stem = os.path.splitext(os.path.basename(file))[0]
    words = stem.replace("-", " ").replace("_", " ").strip()
    return (words[:1].upper() + words[1:]) if words else file


class Compiler:
    """Compiles the edits of one reply, in order. It remembers the nodes it
    has minted, so a later edit can name one as its parent whether or not the
    caller has applied the earlier change yet.

    A new or moved node names its parent (``under``) and the applier places it
    as that parent's last child when the op applies. So the changes of one
    reply land the same whether the caller applies each before compiling the
    next or compiles them all first.

    ``hashes`` maps ``(file, symbol)`` to ``(fingerprint, types_hash)`` for the
    chunks the caller knows; a binding outside it is attached without hashes.
    ``bindable``, when given, is the set of ``(file, symbol)`` an edit may bind;
    anything else is dropped (a model may only place the code it was shown)."""

    def __init__(self, store: Store, *, hashes: dict | None = None, bindable: set | None = None) -> None:
        self.store = store
        self.hashes = hashes or {}
        self.bindable = bindable
        self.minted: dict[str, str] = {}                 # temporary id -> heading id
        self.added: set[str] = set()                     # heading ids minted here

    # ── entry ──────────────────────────────────────────────────────────────
    def compile(self, raw: dict) -> Change:
        err = validate(raw)
        if err:
            return Change(str(raw.get("kind") if isinstance(raw, dict) else ""), refused=err)
        change = getattr(self, "_" + raw["kind"])(raw)
        change.note = raw.get("rationale") or change.note
        return change

    # ── the edits ──────────────────────────────────────────────────────────
    def _attach(self, raw: dict) -> Change:
        fid = self._feature(raw["feature_id"])
        if fid is None:
            return Change("attach", refused=f"unknown feature {raw['feature_id']!r}")
        binds = self._binds(raw.get("bindings"))
        if not binds:
            return Change("attach", fid, refused="no bindable symbol")
        return Change("attach", fid, bodies=[self._bind(fid, f, s) for f, s in binds], bound=binds)

    def _detach(self, raw: dict) -> Change:
        """Only symbols the feature owns now; anything else is not this
        feature's to release."""
        fid = self._feature(raw["feature_id"])
        if fid is None:
            return Change("detach", refused=f"unknown feature {raw['feature_id']!r}")
        owned: list[tuple[str, str]] = []
        for raw_bind in raw.get("bindings") or []:
            key = symbol(raw_bind)
            bound = self.store.bindings.get(key) if key else None
            if bound is not None and bound.feature == fid and key not in owned:
                owned.append(key)
        if not owned:
            return Change("detach", fid, refused="the feature owns none of those symbols")
        return Change("detach", fid, bodies=[{"kind": K.BIND_DETACH, "target": {"fid": fid},
                                              "payload": {"file": f, "symbol": s}} for f, s in owned])

    def _add_node(self, raw: dict) -> Change:
        specs = [BlockSpec(HEADING, 1, one_line(raw["title"]))]
        specs += [s for s in block_md.parse(raw.get("description") or "", headings=False)
                  if s.kind in PROSE or s.kind in SOURCE]
        bodies, ids = block_bodies(specs, after=START)
        fid = ids[0]
        # The heading goes under its parent; its description follows it.
        bodies[0] = {"kind": K.BLOCK_ADD, "target": {"bid": fid},
                     "payload": {"kind": HEADING, "under": self._feature(raw.get("parent_id")) or START}}
        binds = self._binds(raw.get("bindings"))
        bodies += [self._bind(fid, f, s) for f, s in binds]
        if raw.get("id"):
            self.minted[raw["id"]] = fid
        self.added.add(fid)
        return Change("add_node", fid, bodies=bodies, bound=binds, structural=True)

    def _amend(self, raw: dict) -> Change:
        fid = self._feature(raw["feature_id"])
        if fid is None or fid not in self.store.blocks:
            return Change("amend", refused=f"unknown feature {raw['feature_id']!r}")
        change = Change("amend", fid)
        if (raw.get("title") or "").strip():
            change.rewrites.append(Rewrite(fid, "text", one_line(raw["title"])))
        body = {b.id: b for b in self.store.feature_body(fid)[1:] if b.kind in PROSE or b.kind in SOURCE}
        last = fid
        for entry in raw.get("blocks") or []:
            bid, text = entry.get("id"), entry.get("text") or ""
            specs = block_md.parse(text, headings=False, absolute=True)
            if bid is not None:
                block = body.get(bid)
                if block is None:
                    continue            # not a block of this feature's description
                if entry.get("delete") or not specs:
                    change.strikes.append(bid)
                    change.structural = True
                else:
                    err = self._rewrite(change, block, specs[0])
                    if err:
                        change.refused = f"{bid}: {err}"
                        return change
                    specs = specs[1:]   # anything after the first block is new, placed after it
                last = bid
            specs = [s for s in specs if s.kind in PROSE or s.kind in SOURCE]
            if specs:
                bodies, ids = block_bodies(specs, after=last)
                change.bodies = concat_bodies(change.bodies, bodies)
                change.structural = True
                last = ids[-1]
        return change

    def _move_node(self, raw: dict) -> Change:
        fid = self._feature(raw["feature_id"])
        if fid is None or fid not in self.store.blocks:
            return Change("move_node", refused=f"unknown feature {raw['feature_id']!r}")
        parent = self._feature(raw.get("parent_id"))
        if parent in self.store.dominated_ids(fid):
            return Change("move_node", fid, refused="a feature cannot move under itself")
        return Change("move_node", fid, structural=True, bodies=[
            {"kind": K.BLOCK_MOVE, "target": {"bid": fid}, "payload": {"under": parent or START}}])

    def _retire_node(self, raw: dict) -> Change:
        fid = self._feature(raw["feature_id"])
        if fid is None or fid not in self.store.blocks:
            return Change("retire_node", refused=f"unknown feature {raw['feature_id']!r}")
        return Change("retire_node", fid, strikes=[b.id for b in self.store.feature_body(fid)], structural=True)

    # ── helpers ────────────────────────────────────────────────────────────
    def _feature(self, ref: str | None) -> str | None:
        """A feature id from a real id or a temporary one; None when it names
        no live heading (a node minted here counts as live)."""
        if ref in self.minted:
            return self.minted[ref]
        if ref in self.added:
            return ref
        b = self.store.blocks.get(ref) if ref else None
        return ref if b is not None and b.live and b.kind == HEADING else None

    def _binds(self, raw_binds) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for b in raw_binds or []:
            key = symbol(b)
            if key and key not in out and (self.bindable is None or key in self.bindable):
                out.append(key)
        return out

    def _bind(self, fid: str, file: str, sym: str) -> dict:
        fingerprint, types_hash = self.hashes.get((file, sym), ("", ""))
        return {"kind": K.BIND_ATTACH, "target": {"fid": fid},
                "payload": {"file": file, "symbol": sym, "fingerprint": fingerprint, "types_hash": types_hash}}

    def _rewrite(self, change: Change, block, spec: BlockSpec) -> str | None:
        """Make ``block`` read as ``spec``. A prose block may change marker
        (paragraph, bullet, numbered) and keeps its level; a table, diagram or
        snippet must stay what it is. Returns why it cannot, or None."""
        if block.kind in SOURCE:
            if spec.kind != block.kind:
                return f"a {block.kind} cannot become a {spec.kind}"
            change.rewrites.append(Rewrite(block.id, "source", spec.text))
            return None
        if spec.kind not in PROSE:
            return f"a {block.kind} cannot become a {spec.kind}"
        if spec.kind != block.kind:
            payload = {"value": spec.kind, **({"level": spec.level} if spec.kind in ITEMS else {})}
            change.bodies = concat_bodies(change.bodies, [
                {"kind": K.BLOCK_SET, "target": {"bid": block.id, "path": "kind"}, "payload": payload}])
            change.structural = True
        elif spec.kind in ITEMS and spec.level != block.level:
            change.bodies = concat_bodies(change.bodies, [
                {"kind": K.BLOCK_SET, "target": {"bid": block.id, "path": "level"}, "payload": {"value": spec.level}}])
            change.structural = True
        change.rewrites.append(Rewrite(block.id, "text", spec.text))
        return None
