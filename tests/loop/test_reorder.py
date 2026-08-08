"""Sibling reorder — the gesture that used to animate and silently revert.

Siblings were ordered by ``created_at``, so a reorder emitted a ``move`` whose
``parent_id`` had not changed, ``apply_op`` wrote the parent it already had, and
the next render put the node back. These pin the order actually surviving, and
pin the neighbour-identity contract that makes it survive concurrent change too.
"""
from __future__ import annotations

from codoc.loop.apply import apply_op
from codoc.model.event import NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import Store, open_store


def _tree(codoc_dir, *titles: str, parent: str | None = None) -> Store:
    s = open_store(codoc_dir)
    for i, t in enumerate(titles):
        s.upsert_feature(Feature(id=f"f-{i}", title=t, parent_id=parent,
                                 rank=s.rank_for_append(parent)))
    return s


def _order(s: Store, parent=None) -> list[str]:
    return [f.title for f in s.children(parent)]


def test_features_come_back_in_the_order_they_were_added(tmp_path):
    s = _tree(tmp_path, "one", "two", "three")
    assert _order(s) == ["one", "two", "three"]
    s.close()


def test_a_reorder_survives_the_next_read(tmp_path):
    """The whole point. Move the last node to the front and read it back."""
    s = _tree(tmp_path, "one", "two", "three")
    apply_op(NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id="f-2", parent_id=None,
                    before_id="f-0"), s, source="user", applied=True)
    assert _order(s) == ["three", "one", "two"]
    s.close()


def test_a_reorder_touches_exactly_one_row(tmp_path):
    """Load-bearing for the conflict machinery. A dense integer ord would
    renumber every sibling, so dragging one node would mark all of them as
    freshly written — and the author's next edit to any of them would read as a
    conflict with a stranger (loop_b._resolve_content + feature_writers)."""
    s = _tree(tmp_path, "one", "two", "three")
    before = {f.id: (f.rank, f.updated_at.to_str()) for f in s.children(None)}

    apply_op(NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id="f-2", parent_id=None,
                    before_id="f-0"), s, source="user", applied=True)

    after = {f.id: (f.rank, f.updated_at.to_str()) for f in s.children(None)}
    changed = [fid for fid in before if before[fid] != after[fid]]
    assert changed == ["f-2"]
    s.close()


def test_dropping_between_two_siblings_lands_between_them(tmp_path):
    s = _tree(tmp_path, "one", "two", "three", "four")
    apply_op(NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id="f-3", parent_id=None,
                    after_id="f-0", before_id="f-1"), s, source="user", applied=True)
    assert _order(s) == ["one", "four", "two", "three"]
    s.close()


def test_a_move_with_no_neighbours_appends_as_it_always_did(tmp_path):
    """No opinion about order — a plain reparent, the CLI, a legacy command."""
    s = open_store(tmp_path)
    s.upsert_feature(Feature(id="p", title="parent", rank=s.rank_for_append(None)))
    for i, t in enumerate(["a", "b"]):
        s.upsert_feature(Feature(id=f"c-{i}", title=t, parent_id="p",
                                 rank=s.rank_for_append("p")))
    s.upsert_feature(Feature(id="x", title="mover", rank=s.rank_for_append(None)))

    apply_op(NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id="x", parent_id="p"),
             s, source="user", applied=True)

    assert _order(s, "p") == ["a", "b", "mover"]
    s.close()


def test_moving_across_parents_re_ranks_even_with_no_neighbours(tmp_path):
    """A rank is a position among ONE parent's children. Carrying it across
    would place the node by a key that means nothing where it landed —
    sometimes plausibly, which is worse than always wrongly."""
    s = open_store(tmp_path)
    s.upsert_feature(Feature(id="p1", title="p1", rank=s.rank_for_append(None)))
    s.upsert_feature(Feature(id="p2", title="p2", rank=s.rank_for_append(None)))
    # A child of p1 with a deliberately LOW rank, and children of p2 above it.
    s.upsert_feature(Feature(id="x", title="mover", parent_id="p1", rank="1"))
    for i, t in enumerate(["a", "b"]):
        s.upsert_feature(Feature(id=f"c-{i}", title=t, parent_id="p2", rank=f"{i+2}"))

    apply_op(NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id="x", parent_id="p2"),
             s, source="user", applied=True)

    assert _order(s, "p2") == ["a", "b", "mover"]   # appended, not sorted to the front
    s.close()


# -- neighbour identity vs a positional index --------------------------------

def test_a_neighbour_that_vanished_does_not_misplace_the_node(tmp_path):
    """The author dropped the node after "two", and "two" was retired before the
    command drained. The remaining half of the intent still applies."""
    s = _tree(tmp_path, "one", "two", "three")
    apply_op(NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id="f-1"),
             s, source="loop_a", applied=True)

    apply_op(NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id="f-2", parent_id=None,
                    after_id="f-1", before_id="f-0"), s, source="user", applied=True)

    assert _order(s) == ["three", "one"]   # honoured "before one"
    s.close()


def test_both_neighbours_gone_appends_rather_than_guessing(tmp_path):
    s = _tree(tmp_path, "one", "two", "three")
    for gone in ("f-0", "f-1"):
        apply_op(NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id=gone),
                 s, source="loop_a", applied=True)
    s.upsert_feature(Feature(id="f-9", title="late", rank=s.rank_for_append(None)))

    apply_op(NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id="f-2", parent_id=None,
                    after_id="f-0", before_id="f-1"), s, source="user", applied=True)

    assert _order(s) == ["late", "three"]
    s.close()


def test_a_neighbour_in_a_DIFFERENT_parent_is_not_a_position(tmp_path):
    """A stale command naming a sibling that has since been reparented. Its rank
    is a position among other children now, so using it would drop the node at an
    arbitrary place in a list it was never measured against."""
    s = open_store(tmp_path)
    s.upsert_feature(Feature(id="p", title="p", rank=s.rank_for_append(None)))
    s.upsert_feature(Feature(id="a", title="a", parent_id="p", rank="2"))
    s.upsert_feature(Feature(id="elsewhere", title="elsewhere", parent_id=None, rank="1"))
    s.upsert_feature(Feature(id="x", title="mover", parent_id=None,
                             rank=s.rank_for_append(None)))

    apply_op(NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id="x", parent_id="p",
                    after_id="elsewhere"), s, source="user", applied=True)

    assert _order(s, "p") == ["a", "mover"]   # appended, not placed by a foreign rank
    s.close()


def test_a_sibling_arriving_mid_span_still_lands_the_node_in_that_span(tmp_path):
    """The author saw one|three and dropped between them; "two" arrived in the
    gap before the command drained. An index would now be meaningless, but the
    named span still is one — the node lands inside it, wherever that now is."""
    s = _tree(tmp_path, "one", "two", "three", "four")
    apply_op(NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id="f-3", parent_id=None,
                    after_id="f-0", before_id="f-2"), s, source="user", applied=True)

    order = _order(s)
    assert order.index("one") < order.index("four") < order.index("three")
    s.close()


def test_neighbours_that_have_swapped_keep_the_specific_half(tmp_path):
    """The two named siblings are no longer in the order the author saw them —
    somebody reordered THEM in between, so the span is inverted and meaningless.
    "After A" names where the author let go, and is the half worth keeping."""
    s = _tree(tmp_path, "one", "two", "three")
    # The author dropped after "three" and before "one"; those two then swapped,
    # so after_id now sorts AFTER before_id.
    apply_op(NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id="f-1", parent_id=None,
                    after_id="f-2", before_id="f-0"), s, source="user", applied=True)

    order = _order(s)
    assert order.index("two") == order.index("three") + 1   # landed after "three"
    s.close()


def test_a_new_node_lands_where_it_was_typed_not_at_the_end(tmp_path):
    """ADD carries order too - typing a heading in the middle of the document
    must not file the feature at the bottom of its parent."""
    s = _tree(tmp_path, "one", "two", "three")
    apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title="inserted", parent_id=None,
                    after_id="f-0", before_id="f-1"), s, source="user", applied=True)
    assert _order(s) == ["one", "inserted", "two", "three"]
    s.close()


def test_repeated_reordering_never_loses_or_duplicates_a_node(tmp_path):
    """The messy-sequence floor: drag the same node around many times."""
    s = _tree(tmp_path, *[f"n{i}" for i in range(8)])
    titles = set(_order(s))
    for i in range(40):
        target = f"f-{i % 8}"
        anchor = f"f-{(i * 3 + 1) % 8}"
        if target == anchor:
            continue
        apply_op(NodeOp(kind=NodeOpKind.MOVE_NODE, feature_id=target, parent_id=None,
                        before_id=anchor), s, source="user", applied=True)
        order = _order(s)
        assert set(order) == titles and len(order) == len(titles)
    s.close()


# -- end to end: the gesture reaches the rendered document -------------------

def test_a_reorder_command_changes_the_rendered_order(tmp_path):
    """The whole loop, which is where the original defect actually showed: the
    user drags, a command drains, and the NEXT render must show the new order.
    Before ranks existed this reverted, because render read created_at."""
    from codoc.codoc_file.render import render_tree
    from codoc.loop.edits import Command, append_command
    from codoc.loop.loop_b import run_loop_b

    root = tmp_path / "repo"; root.mkdir()
    codoc_dir = tmp_path / ".codoc"; codoc_dir.mkdir()

    s = open_store(codoc_dir)
    for i, t in enumerate(["Alpha", "Beta", "Gamma"]):
        s.upsert_feature(Feature(id=f"f-{i}", title=t, rank=s.rank_for_append(None)))
    from codoc.codoc_file.render import write_tree
    write_tree(s, codoc_dir)
    s.close()

    append_command(codoc_dir, Command(
        id="c-1", kind="move", feature_id="f-2", session="sess-a",
        payload={"parent_id": None, "before_id": "f-0"}))
    res = run_loop_b(str(root), str(codoc_dir), dry_run=False)
    assert not res.error

    s = open_store(codoc_dir)
    assert [f.title for f in s.children(None)] == ["Gamma", "Alpha", "Beta"]
    text = render_tree(s)
    assert text.index("Gamma") < text.index("Alpha") < text.index("Beta")
    s.close()


def test_the_migration_preserves_the_order_users_already_see(tmp_path):
    """Upgrading must not reshuffle anybody's tree. A pre-rank database ordered
    siblings by created_at; the backfill has to reproduce exactly that, or the
    first render after upgrading looks like every feature moved."""
    import sqlite3

    db = tmp_path / "codoc.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE features (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            parent_id TEXT, lifecycle TEXT NOT NULL DEFAULT 'active',
            retired INTEGER NOT NULL DEFAULT 0, realized INTEGER NOT NULL DEFAULT 1,
            local_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE bindings (id TEXT PRIMARY KEY, feature_id TEXT NOT NULL, file TEXT NOT NULL,
            symbol_path TEXT NOT NULL, fingerprint TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(file, symbol_path));
        CREATE TABLE events (id TEXT PRIMARY KEY, at TEXT NOT NULL, source TEXT NOT NULL,
            op_json TEXT NOT NULL, applied INTEGER NOT NULL DEFAULT 1, accepted_at TEXT);
        """
    )
    def _hlc(ms: int) -> str:
        return f"{ms:020d}-{0:020d}-n"

    order = ["zulu", "alpha", "mike", "bravo"]     # deliberately not alphabetical
    for i, t in enumerate(order):
        conn.execute(
            "INSERT INTO features (id,title,parent_id,created_at,updated_at) VALUES (?,?,?,?,?)",
            (f"f-{i}", t, None, _hlc(1000 + i), _hlc(1000 + i)),
        )
    conn.commit(); conn.close()

    s = Store(db).open()
    assert [f.title for f in s.children(None)] == order
    assert all(f.rank for f in s.children(None))   # and everyone got a key
    s.close()


def test_retiring_a_parent_keeps_its_children_together_in_order(tmp_path):
    """Promoted children carried a rank scoped to the RETIRED parent, so they
    interleaved arbitrarily among the grandparent's siblings. A cross-parent
    promotion must re-rank, exactly like MOVE_NODE does."""
    s = _tree(tmp_path, "g1", "mid", "g2")
    s.upsert_feature(Feature(id="c-0", title="c1", parent_id="f-1",
                             rank=s.rank_for_append("f-1")))
    s.upsert_feature(Feature(id="c-1", title="c2", parent_id="f-1",
                             rank=s.rank_for_append("f-1")))
    apply_op(NodeOp(kind=NodeOpKind.RETIRE_NODE, feature_id="f-1"),
             s, source="user", applied=True)
    assert _order(s) == ["g1", "g2", "c1", "c2"]
    s.close()


def test_an_add_command_lands_where_it_was_typed(tmp_path):
    """The add command's after_id/before_id must survive the command→op mapping
    (the move branch mapped them; the add branch dropped them, so a heading
    typed mid-document teleported to the end of its parent)."""
    from codoc.codoc_file.render import write_tree
    from codoc.loop.edits import Command, append_command
    from codoc.loop.loop_b import run_loop_b

    root = tmp_path / "repo"; root.mkdir()
    codoc_dir = tmp_path / ".codoc"; codoc_dir.mkdir()
    s = open_store(codoc_dir)
    for i, t in enumerate(["Alpha", "Beta"]):
        s.upsert_feature(Feature(id=f"f-{i}", title=t, rank=s.rank_for_append(None)))
    write_tree(s, codoc_dir)
    s.close()

    append_command(codoc_dir, Command(
        id="c-add-mid", kind="add", local_id="L-mid",
        payload={"title": "Mid", "description": "",
                 "after_id": "f-0", "before_id": "f-1"}))
    res = run_loop_b(str(root), str(codoc_dir), dry_run=False)
    assert not res.error

    s = open_store(codoc_dir)
    assert [f.title for f in s.children(None)] == ["Alpha", "Mid", "Beta"]
    s.close()


def test_a_torn_rank_migration_heals_on_the_next_open(tmp_path):
    """sqlite3 auto-commits the ALTER separately from the backfill UPDATEs, so a
    crash in between leaves the column present with every rank ''. Gating the
    backfill on column EXISTENCE then skips it forever (every drag appends to
    the end, silently). The backfill must be gated on the DATA."""
    import sqlite3

    db = tmp_path / "codoc.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE features (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            parent_id TEXT, lifecycle TEXT NOT NULL DEFAULT 'active',
            retired INTEGER NOT NULL DEFAULT 0, realized INTEGER NOT NULL DEFAULT 1,
            local_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE bindings (id TEXT PRIMARY KEY, feature_id TEXT NOT NULL, file TEXT NOT NULL,
            symbol_path TEXT NOT NULL, fingerprint TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(file, symbol_path));
        CREATE TABLE events (id TEXT PRIMARY KEY, at TEXT NOT NULL, source TEXT NOT NULL,
            op_json TEXT NOT NULL, applied INTEGER NOT NULL DEFAULT 1, accepted_at TEXT);
        """
    )
    def _hlc(ms: int) -> str:
        return f"{ms:020d}-{0:020d}-n"

    order = ["zulu", "alpha", "mike"]
    for i, t in enumerate(order):
        conn.execute(
            "INSERT INTO features (id,title,parent_id,created_at,updated_at) VALUES (?,?,?,?,?)",
            (f"f-{i}", t, None, _hlc(1000 + i), _hlc(1000 + i)),
        )
    # The torn state: the ALTER committed, the backfill did not.
    conn.execute("ALTER TABLE features ADD COLUMN rank TEXT NOT NULL DEFAULT ''")
    conn.commit(); conn.close()

    s = Store(db).open()
    assert [f.title for f in s.children(None)] == order
    assert all(f.rank for f in s.children(None))
    s.close()


def test_migration_breaks_created_at_ties_by_insertion_order(tmp_path):
    """Same-millisecond created_at is the COMMON case (HLC logical time is 0 and
    bootstrap mints sibling batches in a tight loop). The old ORDER BY created_at
    scans resolved those ties by rowid; tie-breaking by the random id fragment
    would reshuffle every such batch on upgrade."""
    import sqlite3

    db = tmp_path / "codoc.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE features (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            parent_id TEXT, lifecycle TEXT NOT NULL DEFAULT 'active',
            retired INTEGER NOT NULL DEFAULT 0, realized INTEGER NOT NULL DEFAULT 1,
            local_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE bindings (id TEXT PRIMARY KEY, feature_id TEXT NOT NULL, file TEXT NOT NULL,
            symbol_path TEXT NOT NULL, fingerprint TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(file, symbol_path));
        CREATE TABLE events (id TEXT PRIMARY KEY, at TEXT NOT NULL, source TEXT NOT NULL,
            op_json TEXT NOT NULL, applied INTEGER NOT NULL DEFAULT 1, accepted_at TEXT);
        """
    )
    hlc = f"{1000:020d}-{0:020d}-n"  # ONE timestamp for the whole batch
    rows = [("f-9", "first"), ("f-2", "second"), ("f-7", "third"), ("f-0", "fourth")]
    for fid, t in rows:  # ids deliberately NOT in insertion-sorted order
        conn.execute(
            "INSERT INTO features (id,title,parent_id,created_at,updated_at) VALUES (?,?,?,?,?)",
            (fid, t, None, hlc, hlc),
        )
    conn.commit(); conn.close()

    s = Store(db).open()
    assert [f.title for f in s.children(None)] == ["first", "second", "third", "fourth"]
    s.close()
