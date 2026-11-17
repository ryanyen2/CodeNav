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
import re
from dataclasses import dataclass, field

from codoc.calculus import block_md
from codoc.calculus import ops as K
from codoc.calculus.block_md import BlockSpec
from codoc.calculus.doc import HEADING, ITEMS, MAX_LEVEL, PROSE, SOURCE
from codoc.calculus.ids import START
from codoc.calculus.ingest import block_bodies, concat_bodies
from codoc.calculus.store import Store

KINDS = ("attach", "detach", "add_node", "amend", "move_node", "retire_node")


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


_ITEM_LINE = re.compile(r"^([ \t]*)(?:[-+*]|\d{1,9}\.) +")


def absolute_levels(md: str, specs: list[BlockSpec]) -> list[BlockSpec]:
    """``specs`` with each list item's level read from its own indentation:
    level 1 flush left, one more per started four columns. A block is shown
    to the model on its own with that indentation (``block_md.render_one``),
    so the level it writes back is absolute, not relative to the block before."""
    indents = [len(m.group(1).expandtabs(4)) for line in md.split("\n") if (m := _ITEM_LINE.match(line))]
    out, i = [], 0
    for s in specs:
        if s.kind in ITEMS and i < len(indents):
            s = BlockSpec(s.kind, min(MAX_LEVEL, 1 + -(-indents[i] // 4)), s.text, s.content, s.id)
            i += 1
        out.append(s)
    return out


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

    ``hashes`` maps ``(file, symbol)`` to ``(fingerprint, types_hash)`` for the
    chunks the caller knows; a binding outside it is attached without hashes.
    ``bindable``, when given, is the set of ``(file, symbol)`` an edit may bind;
    anything else is dropped (a model may only place the code it was shown)."""

    def __init__(self, store: Store, *, hashes: dict | None = None, bindable: set | None = None) -> None:
        self.store = store
        self.hashes = hashes or {}
        self.bindable = bindable
        self.minted: dict[str, str] = {}                 # temporary id -> heading id
        self._own: dict[str, tuple[str, int]] = {}       # minted heading id -> (its last own block, its level)
        self._next: dict[str, tuple[str, int]] = {}      # anchor -> (last block of the node placed after it, that node's level)

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
        after, level = self._place(raw.get("parent_id"))
        specs = [BlockSpec(HEADING, level, one_line(raw["title"]))]
        specs += [s for s in block_md.parse(raw.get("description") or "", headings=False)
                  if s.kind in PROSE or s.kind in SOURCE]
        bodies, ids = block_bodies(specs, after=after)
        fid = ids[0]
        binds = self._binds(raw.get("bindings"))
        bodies += [self._bind(fid, f, s) for f, s in binds]
        if raw.get("id"):
            self.minted[raw["id"]] = fid
        self._own[fid] = (ids[-1], level)
        self._link(after, ids[-1], level)
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
            specs = absolute_levels(text, block_md.parse(text, headings=False))
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
        after, level = self._place(raw.get("parent_id"))
        moved = self.store.feature_range(fid)
        if after in {b.id for b in moved}:
            return Change("move_node", fid, refused="a feature cannot move under itself")
        self._link(after, moved[-1].id, level)
        delta = level - self.store.blocks[fid].level
        return Change("move_node", fid, structural=True, bodies=[
            {"kind": K.BLOCK_MOVE, "target": {"bid": fid}, "payload": {"after": after, "level_delta": delta}}])

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
        if ref in self._own:
            return ref
        b = self.store.blocks.get(ref) if ref else None
        return ref if b is not None and b.live and b.kind == HEADING else None

    def _place(self, parent_ref: str | None) -> tuple[str, int]:
        """Where a child of ``parent_ref`` goes: ``(after, level)``. After the
        last block under the parent, one level deeper; with no parent, at the
        end of the document at level 1.

        The store may not hold what this compiler placed earlier (a caller may
        submit a whole reply at once), so the anchor is read from the store and
        then walked along ``_next`` to the end of what was placed after it."""
        parent = self._feature(parent_ref)
        if parent in self._own:
            after, level = self._own[parent][0], self._own[parent][1] + 1
        elif parent is not None:
            after, level = self.store.feature_range(parent)[-1].id, self.store.blocks[parent].level + 1
        else:
            live = self.store.ordered()
            after, level = (live[-1].id if live else START), 1
        # Walk past the nodes placed after the anchor that are deeper than the
        # parent: they are inside its subtree. A node at the parent's level or
        # shallower is a sibling of it or above, and the child goes before it.
        seen = {after}
        while after in self._next and self._next[after][1] >= level and self._next[after][0] not in seen:
            after = self._next[after][0]
            seen.add(after)
        return after, level

    def _link(self, after: str, last: str, level: int) -> None:
        """Record a node placed right after ``after``, ending at ``last``.
        Whatever this compiler placed after ``after`` before now follows the
        new node, so the chain stays in document order."""
        old = self._next.get(after)
        self._next[after] = (last, level)
        if old is not None and old[0] != last:
            self._next[last] = old

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
