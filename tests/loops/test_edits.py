"""Tree edits compile to op bodies, ingests and strikes (codoc.loops.edits)."""
from __future__ import annotations

from codoc.calculus import ops as K
from codoc.calculus.apply import apply
from codoc.calculus.derive import description, title
from codoc.calculus.doc import BULLET, NUMBERED, PARAGRAPH, TABLE
from codoc.calculus.ingest import ingest, resolve_pending_refs
from codoc.calculus.store import Store
from codoc.calculus.ingest import strike_bodies
from codoc.loops.edits import Change, Compiler, validate
from tests.helpers import Mint


def submit(store: Store, m: Mint, bodies: list[dict], actor="loop", plan=None) -> None:
    """Apply one batch the way ``Workspace.submit`` does."""
    inserts: list[str] = []
    for b in bodies:
        payload = dict(b["payload"])
        resolve_pending_refs(payload, inserts)
        op = apply(store, m.op(b["kind"], b["target"], payload, actor=actor, plan=plan))
        assert op.conflict is None, (b, op.conflict)
        if b["kind"] == K.TEXT_INSERT:
            inserts.append(op.id)


def land(store: Store, m: Mint, change: Change, actor="loop") -> None:
    """Apply a change on the trunk: its batch, then each rewrite as an ingest."""
    assert change.refused is None, change.refused
    submit(store, m, change.bodies, actor)
    for r in change.rewrites:
        b = store.blocks[r.bid]
        res = ingest(b.text(r.path), bid=r.bid, path=r.path, base_text=b.text(r.path).live_text(), new_md=r.new_md,
                     actor=actor, multiline=r.path == "source", raw=r.path == "source")
        assert res.refused is None, res.refused
        submit(store, m, res.bodies, actor)


SKETCH = """Sends a prepared request over a pooled connection.

- One pool per `(host, proxy)` pair.
- Retries are off by default (`max_retries=0`).

| status | action |
|---|---|
| `2xx` | return the body |
| `429` | wait, then resend |"""


def kinds(store: Store, fid: str) -> list[tuple[str, int]]:
    return [(b.kind, b.level) for b in store.feature_body(fid)[1:]]


def test_add_node_writes_the_description_as_blocks_and_binds_the_code():
    s, m = Store(), Mint()
    c = Compiler(s, hashes={("a.py", "a.py::send"): ("fp", "th")})
    change = c.compile({"kind": "add_node", "id": "n1", "title": "HTTP adapter", "description": SKETCH,
                        "bindings": ["a.py::send", ["a.py", "a.py::recv"], "not a symbol"], "rationale": "main class"})
    assert change.structural and change.note == "main class"
    land(s, m, change)
    fid = change.target
    assert title(s, fid) == "HTTP adapter" and description(s, fid) == SKETCH
    assert kinds(s, fid) == [(PARAGRAPH, 0), (BULLET, 1), (BULLET, 1), (TABLE, 0)]
    assert change.bound == [("a.py", "a.py::send"), ("a.py", "a.py::recv")]
    assert s.bindings[("a.py", "a.py::send")].fingerprint == "fp" and s.bindings[("a.py", "a.py::recv")].feature == fid


def test_a_reply_can_nest_under_a_node_it_just_created_without_applying_first():
    """An MCP caller compiles a whole reply against a read-only store and
    submits at the end, so placement cannot depend on the store having the
    earlier nodes."""
    s, m = Store(), Mint()
    c = Compiler(s)
    replies = [
        {"kind": "add_node", "id": "n1", "title": "Parent", "description": "P."},
        {"kind": "add_node", "id": "n2", "parent_id": "n1", "title": "Child one", "description": "- a\n- b"},
        {"kind": "add_node", "parent_id": "n1", "title": "Child two", "description": "C2."},
        {"kind": "add_node", "title": "Second top", "description": "T."},
    ]
    changes = [c.compile(r) for r in replies]
    for ch in changes:                      # applied only now, in order
        land(s, m, ch)
    assert [(title(s, h.id), h.level) for h in s.headings()] == [
        ("Parent", 1), ("Child one", 2), ("Child two", 2), ("Second top", 1)]
    assert description(s, changes[1].target) == "- a\n- b"


def test_a_child_compiled_after_a_sibling_lands_before_that_sibling():
    """[A, B, A1 under A, C], all compiled before any is applied (the MCP
    path), lands as A, A1, B, C."""
    s, m = Store(), Mint()
    c = Compiler(s)
    changes = [c.compile(r) for r in (
        {"kind": "add_node", "id": "a", "title": "A", "description": "a."},
        {"kind": "add_node", "title": "B", "description": "b."},
        {"kind": "add_node", "parent_id": "a", "title": "A1", "description": "a1."},
        {"kind": "add_node", "title": "C", "description": "c."})]
    for ch in changes:
        land(s, m, ch)
    assert [(title(s, h.id), h.level) for h in s.headings()] == [("A", 1), ("A1", 2), ("B", 1), ("C", 1)]


def test_list_levels_in_an_amend_are_read_from_the_indentation_shown():
    """A block is shown with its absolute indentation. Writing a new item with
    the same indentation as a level-2 item makes a level-2 sibling; changing an
    existing item's indentation changes its level."""
    s, m = Store(), Mint()
    ch = Compiler(s).compile({"kind": "add_node", "title": "Retry", "description": "- top\n    - nested"})
    land(s, m, ch)
    top, nested = [b.id for b in s.feature_body(ch.target)[1:]]
    amend = Compiler(s).compile({"kind": "amend", "feature_id": ch.target, "blocks": [
        {"id": nested, "text": "    - nested"}, {"text": "    - sibling"}, {"id": top, "text": "    - top, now nested"}]})
    land(s, m, amend)
    assert kinds(s, ch.target) == [(BULLET, 2), (BULLET, 2), (BULLET, 2)]


def test_moves_under_a_new_theme_keep_their_order():
    """The organize pass adds a theme, then moves features under it in the
    order it lists them."""
    s, m = Store(), Mint()
    c = Compiler(s)
    a, b = (c.compile({"kind": "add_node", "title": t, "description": "x."}) for t in ("Alpha", "Beta"))
    for ch in (a, b):
        land(s, m, ch)
    theme = c.compile({"kind": "add_node", "id": "t1", "title": "Theme", "description": "Both."})
    moves = [c.compile({"kind": "move_node", "feature_id": f.target, "parent_id": "t1"}) for f in (a, b)]
    for ch in (theme, *moves):
        land(s, m, ch)
    assert [(title(s, h.id), h.level) for h in s.headings()] == [("Theme", 1), ("Alpha", 2), ("Beta", 2)]


def _seeded():
    s, m = Store(), Mint()
    c = Compiler(s)
    ch = c.compile({"kind": "add_node", "title": "Queue", "description":
                    "Holds edits waiting to be built.\n\n- Writes in place.\n- A missing file is an empty queue.\n\n| a | b |\n|---|---|\n| 1 | 2 |"})
    land(s, m, ch)
    fid = ch.target
    return s, m, fid, [b.id for b in s.feature_body(fid)[1:]]


def test_amend_rewrites_named_blocks_adds_new_ones_and_strikes():
    s, m, fid, (para, b1, b2, table) = _seeded()
    change = Compiler(s).compile({"kind": "amend", "feature_id": fid, "title": "Realize queue", "blocks": [
        {"id": b1, "text": "1. Write to a temporary file."},                 # bullet -> numbered, reworded
        {"text": "2. Rename it over the old file."},                         # new block after b1
        {"id": b2, "delete": True},
        {"id": table, "text": "| a | b |\n|---|---|\n| 1 | 3 |"},
        {"id": "b-000000000000", "text": "ignored: not a block of this feature"},
    ]})
    assert change.refused is None and change.structural
    assert change.strikes == [b2]
    assert [(r.bid, r.path) for r in change.rewrites] == [(fid, "text"), (b1, "text"), (table, "source")]
    land(s, m, change)
    assert title(s, fid) == "Realize queue"
    assert kinds(s, fid) == [(PARAGRAPH, 0), (NUMBERED, 1), (NUMBERED, 1), (BULLET, 1), (TABLE, 0)]
    assert "1. Write to a temporary file.\n2. Rename it over the old file." in description(s, fid)
    assert "| 1 | 3 |" in description(s, fid)
    # a strike only exists under a plan
    marks = strike_bodies(s, change.strikes, "p-1")
    assert [(b["target"]["bid"], b["payload"]["kind"], b["payload"]["attrs"]) for b in marks] == [(b2, "suggest_del", {"plan": "p-1"})]


def test_amend_that_only_rewords_is_not_structural():
    s, m, fid, (para, b1, _b2, _table) = _seeded()
    change = Compiler(s).compile({"kind": "amend", "feature_id": fid, "blocks": [
        {"id": b1, "text": "- Writes through a temporary file."}]})
    assert not change.structural and not change.bodies and [r.new_md for r in change.rewrites] == ["Writes through a temporary file."]


def test_an_indented_new_item_nests_under_the_item_before_it():
    s, m, fid, (_para, b1, _b2, _table) = _seeded()
    change = Compiler(s).compile({"kind": "amend", "feature_id": fid, "blocks": [
        {"id": b1, "text": "- Writes in place."}, {"text": "    - Except on Windows.\n    - And on NFS."}]})
    land(s, m, change)
    assert kinds(s, fid)[1:4] == [(BULLET, 1), (BULLET, 2), (BULLET, 2)]


def test_a_block_cannot_change_medium():
    s, m, fid, (para, _b1, _b2, table) = _seeded()
    c = Compiler(s)
    assert c.compile({"kind": "amend", "feature_id": fid, "blocks": [{"id": table, "text": "now prose"}]}).refused \
        == f"{table}: a table cannot become a paragraph"
    assert c.compile({"kind": "amend", "feature_id": fid, "blocks": [{"id": para, "text": "| x |\n|---|"}]}).refused \
        == f"{para}: a paragraph cannot become a table"


def test_attach_move_retire_and_what_is_refused():
    s, m, fid, body = _seeded()
    c = Compiler(s, bindable={("a.py", "a.py::f")})
    other = c.compile({"kind": "add_node", "title": "Other", "description": "O."})
    land(s, m, other)
    att = c.compile({"kind": "attach", "feature_id": fid, "bindings": ["a.py::f", "b.py::not_shown"]})
    assert att.bound == [("a.py", "a.py::f")] and not att.structural
    assert c.compile({"kind": "attach", "feature_id": fid, "bindings": ["b.py::not_shown"]}).refused == "no bindable symbol"
    mv = c.compile({"kind": "move_node", "feature_id": other.target, "parent_id": fid})
    land(s, m, mv)
    assert [(title(s, h.id), h.level) for h in s.headings()] == [("Queue", 1), ("Other", 2)]
    assert c.compile({"kind": "move_node", "feature_id": fid, "parent_id": other.target}).refused == "a feature cannot move under itself"
    assert c.compile({"kind": "retire_node", "feature_id": fid}).strikes == [fid, *body]
    assert c.compile({"kind": "retire_node", "feature_id": "b-nope"}).refused == "unknown feature 'b-nope'"


def test_validate_names_the_fault():
    assert validate("x") == "not an object"
    assert validate({"kind": "rename"}) == "unknown kind 'rename'"
    assert validate({"kind": "add_node", "title": " "}) == "add_node.title must be a non-empty string"
    assert validate({"kind": "amend"}) == "amend.feature_id must be a string"
    assert validate({"kind": "attach", "feature_id": "b-1", "bindings": []}) == "attach.bindings is empty"
    assert validate({"kind": "amend", "feature_id": "b-1", "blocks": [{}]}) == "amend.blocks[] entry has neither id nor text"
    assert validate({"kind": "amend", "feature_id": "b-1", "blocks": [{"id": "b-2", "delete": True}]}) is None
