"""Builders shared by the test modules.

Two levels:

- On a bare ``Store``: ``Mint`` makes ops for one session, ``heading`` and
  ``paragraph`` add a block with text, ``apply_mark`` marks a range of one
  insert, ``run_ingest`` applies what ``ingest`` returns, ``build`` applies a
  batch of bodies, ``replay`` and ``invert`` rebuild and undo.
- On a ``Workspace``: ``seed`` writes a person's feature ``Client`` with the
  paragraph ``Retries once.`` bound to ``c.py::send``; ``seed_loop`` writes the
  same feature as the loop, with ``Retries once before giving up.``.
"""
import copy
from pathlib import Path

from codoc.calculus import block_md
from codoc.calculus import ops as K
from codoc.calculus.apply import apply, replay_recorded
from codoc.calculus.derive import markdown
from codoc.calculus.doc import HEADING, PARAGRAPH
from codoc.calculus.ids import START
from codoc.calculus.ingest import block_bodies, ingest, resolve_pending_refs
from codoc.calculus.ops import Op
from codoc.calculus.store import Store
from codoc.calculus.text import BOLD
from codoc.index.changes import ChunkRef
from codoc.index.reader import ChunkRow
from codoc.workspace import Workspace
from codoc.workspace import log as L
from codoc.workspace.snapshot import write_snapshot

H = "human:local"


# ── ops on a bare Store ─────────────────────────────────────────────────────

class Clock:
    """One monotone clock for every ``Mint`` in a test, because one applier
    stamps every op it applies and an HLC only goes forward. Two Mints with
    counters of their own would give the second actor's ops stamps before the
    first's, which no store can observe. A test that wants two clones gives
    each its own ``Clock``."""

    def __init__(self) -> None:
        self.n = 0

    def tick(self) -> str:
        self.n += 1
        return f"{self.n:020d}"


_CLOCK = Clock()


class Mint:
    """Mints ops for one session; the clock is shared unless one is given."""

    def __init__(self, session="s", actor="human:a", clock: Clock | None = None):
        self.session, self.actor, self.n = session, actor, 0
        self.clock = clock or _CLOCK

    def op(self, kind, target, payload, *, actor=None, plan=None, batch="b", caused_by=None):
        self.n += 1
        return Op(id=f"{self.session}:{self.n}", at=self.clock.tick(), actor=actor or self.actor, batch=batch,
                  kind=kind, target=dict(target), payload=dict(payload), plan=plan, caused_by=caused_by)


def heading(m: Mint, store: Store, bid: str, title: str, level=1, after=START, plan=None, actor=None):
    apply(store, m.op(K.BLOCK_ADD, {"bid": bid}, {"kind": HEADING, "level": level, "after": after}, plan=plan, actor=actor))
    return apply(store, m.op(K.TEXT_INSERT, {"bid": bid, "path": "text"}, {"after": START, "text": title}, plan=plan, actor=actor))


def paragraph(m: Mint, store: Store, bid: str, body: str, after, plan=None, actor=None):
    apply(store, m.op(K.BLOCK_ADD, {"bid": bid}, {"kind": PARAGRAPH, "after": after}, plan=plan, actor=actor))
    return apply(store, m.op(K.TEXT_INSERT, {"bid": bid, "path": "text"}, {"after": START, "text": body}, plan=plan, actor=actor))


def apply_mark(m: Mint, s: Store, bid: str, ins: Op, lo: int, hi: int, kind=BOLD, attrs=None, actor=None, plan=None):
    """Turn ``kind`` on over characters ``lo..hi`` of the insert ``ins``."""
    return apply(s, m.op(K.TEXT_MARK, {"bid": bid, "path": "text"},
                         {"kind": kind, "attrs": attrs or {}, "start": [ins.id, lo], "end": [ins.id, hi], "on": True},
                         actor=actor, plan=plan))


def replay(store: Store) -> Store:
    """As a rebuild replays: what the record says, not what today would judge."""
    fresh = Store(store.policy)
    for o in store.ops:
        replay_recorded(fresh, Op.from_json(copy.deepcopy(o.to_json())))
    return fresh


def invert(store: Store, m: Mint, op: Op) -> list[Op]:
    """Apply the recorded inverse as new ops (undo)."""
    out = []
    for body in op.inverse:
        out.append(apply(store, m.op(body["kind"], body["target"], body["payload"])))
    return out


def run_ingest(store: Store, m: Mint, bid: str, new_md: str, actor="loop", plan=None, base=None):
    """Ingest ``new_md`` into ``bid``'s text and apply the bodies in order."""
    t = store.blocks[bid].text()
    res = ingest(t, bid=bid, path="text", base_text=t.live_text(), new_md=new_md, actor=actor, trunk=plan is None)
    if res.refused:
        return res, []
    ops = []
    insert_ids = []
    for b in res.bodies:
        op = m.op(b["kind"], b["target"], b["payload"], actor=actor, plan=plan)
        if b["kind"] == K.TEXT_INSERT:
            insert_ids.append(op.id)
        ops.append(op)
    # Marks may name inserts by ordinal; resolve, then apply in order.
    for o in ops:
        resolve_pending_refs(o.payload, insert_ids)
    applied = [apply(store, o) for o in ops]
    return res, applied


def import_markdown(md: str) -> list[dict]:
    """Op bodies that build the document ``md`` describes, keeping the ids it carries."""
    return block_bodies(block_md.parse(md), after=START, keep_ids=True)[0]


def build(store: Store, m: Mint, bodies: list[dict], actor="import"):
    """Apply a batch of bodies the way ``Workspace.submit`` does, resolving ``$ins`` anchors."""
    insert_ids: list[str] = []
    for b in bodies:
        payload = dict(b["payload"])
        resolve_pending_refs(payload, insert_ids)
        op = apply(store, m.op(b["kind"], b["target"], payload, actor=actor))
        assert op.conflict is None, (b, op.conflict)
        if b["kind"] == K.TEXT_INSERT:
            insert_ids.append(op.id)


def texts_after_each_op(store: Store) -> list[dict[str, str]]:
    """Replay the prefix up to each op and read every block."""
    out = []
    fresh = Store()
    for o in store.ops:
        apply(fresh, Op.from_json(copy.deepcopy(o.to_json())))
        # What the page showed: a dead block reads as nothing, whatever text the
        # store still holds so its characters can be named.
        out.append({b.id: (b.text().live_text() if b.live else "") for b in fresh.all_blocks()})
    return out


def T(store: Store, bid: str) -> str:
    """The live text of ``bid``."""
    return store.blocks[bid].text().live_text()


def md_of(store: Store, bid: str) -> str:
    """The inline Markdown of ``bid``'s text."""
    return markdown(store.blocks[bid].text())


def bold_chars(text) -> set:
    out = set()
    for m in text.marks:
        if m.kind == BOLD:
            out.update(text.mark_refs(m))
    return out


# ── a Workspace ─────────────────────────────────────────────────────────────

def seed(ws: Workspace, actor=H, bind=True):
    """``Client`` / ``Retries once.`` written by ``actor``; the loop binds ``c.py::send``."""
    ws.submit([
        {"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": HEADING, "level": 1, "after": START}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "h1", "path": "text"}, "payload": {"after": START, "text": "Client"}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": "h1"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "Retries once."}},
    ], actor=actor)
    if bind:
        ws.submit([{"kind": K.BIND_ATTACH, "target": {"fid": "h1"},
                    "payload": {"file": "c.py", "symbol": "c.py::send", "fingerprint": "f1", "types_hash": "t1"}}], actor="loop")


def seed_loop(ws: Workspace):
    """``Client`` / ``Retries once before giving up.`` bound to ``c.py::send``, all written by the loop."""
    ws.submit([
        {"kind": K.BLOCK_ADD, "target": {"bid": "h1"}, "payload": {"kind": HEADING, "level": 1, "after": START}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "h1", "path": "text"}, "payload": {"after": START, "text": "Client"}},
        {"kind": K.BLOCK_ADD, "target": {"bid": "p1"}, "payload": {"kind": PARAGRAPH, "after": "h1"}},
        {"kind": K.TEXT_INSERT, "target": {"bid": "p1", "path": "text"}, "payload": {"after": START, "text": "Retries once before giving up."}},
        {"kind": K.BIND_ATTACH, "target": {"fid": "h1"}, "payload": {"file": "c.py", "symbol": "c.py::send", "fingerprint": "f1", "types_hash": "t1"}},
    ], actor="loop")


def ws_paragraph(ws: Workspace, bid: str, text: str, after=START, actor=H) -> Op:
    """Add a paragraph holding ``text``; returns the insert."""
    return ws.submit([{"kind": K.BLOCK_ADD, "target": {"bid": bid}, "payload": {"kind": PARAGRAPH, "after": after}},
                      {"kind": K.TEXT_INSERT, "target": {"bid": bid, "path": "text"}, "payload": {"after": START, "text": text}}],
                     actor=actor)[1]


def kind_block(ws: Workspace, kind: str, content: dict, *, after: str, actor=H) -> str:
    """Add a kind block and its text in one batch, as ``codoc_write_block``
    does; returns its id."""
    bodies, (bid,) = block_bodies([block_md.BlockSpec(kind, content=content)], after=after)
    assert all(o.conflict is None for o in ws.submit(bodies, actor=actor))
    return bid


def ins(ws, bid, after, text, actor=H, plan=None):
    return ws.submit([{"kind": K.TEXT_INSERT, "target": {"bid": bid, "path": "text"},
                       "payload": {"after": after, "text": text}}], actor=actor, plan=plan)[0]


def mark(ws, bid, kind, start, end, on=True, actor=H, plan=None, attrs=None):
    return ws.submit([{"kind": K.TEXT_MARK, "target": {"bid": bid, "path": "text"},
                       "payload": {"kind": kind, "attrs": attrs or {}, "start": list(start), "end": list(end), "on": on}}],
                     actor=actor, plan=plan)[0]


def refs(ws, bid):
    return list(ws.store.blocks[bid].text().live_refs())


def write_roles(cd: Path, roles: dict):
    from codoc.workspace.config import write_config
    write_config(cd, roles=roles)


def fold(cd) -> None:
    """Snapshot the workspace and close it."""
    ws = Workspace(cd)
    write_snapshot(cd, ws.store, ws.clock)
    ws.close()


def sub(cid, kind, target, payload, batch="b1", **kw) -> L.Submission:
    return L.Submission(cid=cid, kind=kind, target=target, payload=payload, batch=batch, **kw)


# ── the index and the LLM ───────────────────────────────────────────────────

def chunk(file, sym, fp="", th="", src=""):
    return ChunkRef(file=file, symbol_path=sym, fingerprint=fp, types_hash=th, source=src)


def row(file, sym, src="def f(): ...", th="t"):
    return ChunkRow(id=0, file=file, symbol_path=sym, language="python", source=src, tokens_hash=f"h-{sym}",
                    types_hash=th, start_byte=0, end_byte=1)


ROWS = [row("net/client.py", "net/client.py::Client"), row("net/client.py", "net/client.py::Client.send"),
        row("store/db.py", "store/db.py::open"), row("store/db.py", "store/db.py::_helper")]


def fake_file(file, chunks, edges, titles, **kw):
    """The bootstrap's per-file pass for ``ROWS``."""
    if file == "net/client.py":
        return [{"id": "n1", "kind": "add_node", "title": "HTTP client",
                 "description": "Sends requests.\n\nRetries **once** on timeout.",
                 "bindings": [["net/client.py", "net/client.py::Client"]]},
                {"id": "n2", "kind": "add_node", "parent_id": "n1", "title": "Sending",
                 "description": "The send path.", "bindings": [["net/client.py", "net/client.py::Client.send"]]}]
    return [{"id": "n1", "kind": "add_node", "title": "Store", "description": "Opens the database.",
             "bindings": [["store/db.py", "store/db.py::open"], ["store/db.py", "store/db.py::nope"]]}]


def fake_org(features, edges, **kw):
    """The bootstrap's organising pass for ``ROWS``."""
    return [{"id": "t1", "kind": "add_node", "title": "Networking", "description": "Everything on the wire."},
            {"kind": "move_node", "feature_id": next(f["id"] for f in features if f["title"] == "HTTP client"), "parent_id": "t1"}]
