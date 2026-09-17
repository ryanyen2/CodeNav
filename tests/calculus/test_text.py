"""Text: identity-addressed segments and marks (spec §2, §3)."""
import random

import pytest

from codoc.calculus.ids import START
from codoc.calculus.text import (
    BOLD, CITE, COMMENT, LINK, AnchorError, MarkError, Text,
)


def op(n: int) -> str:
    return f"s:{n}"


def typed(t: Text, s: str, n: int, after=None, actor="human:a"):
    """Insert ``s`` after ``after`` (default: at the live end) as op n."""
    if after is None:
        after = t.last_live_ref() or START
    return t.insert(after, s, op=op(n), actor=actor, at=f"{n:020d}")


def test_insert_mints_identity_and_keeps_live_text():
    t = Text()
    typed(t, "hello", 1)
    typed(t, " world", 2)
    assert t.live_text() == "hello world"
    assert t.ref_at(0) == (op(1), 0)
    assert t.ref_at(6) == (op(2), 1)
    assert t.offset_of((op(2), 5)) == 10


def test_insert_in_the_middle_splits_segment_without_changing_identity():
    t = Text()
    typed(t, "abcd", 1)
    t.insert((op(1), 1), "XY", op=op(2), actor="loop", at="2")
    assert t.live_text() == "abXYcd"
    assert t.ref_at(4) == (op(1), 2)
    assert t.ref_at(2) == (op(2), 0)
    assert len(t.segs) == 3


def test_delete_returns_canonical_segments_and_keeps_tombstones():
    t = Text()
    typed(t, "abcdef", 1)
    killed = t.delete((op(1), 1), (op(1), 3), by=op(2))
    assert killed == [(op(1), 1, 3)]
    assert t.live_text() == "aef"
    assert t.holds((op(1), 2)) and not t.is_live((op(1), 2))
    # An insert after a dead char still lands there (positioned tombstone).
    t.insert((op(1), 2), "Z", op=op(3), actor="human:a", at="3")
    assert t.live_text() == "aZef"
    t.revive(killed, by=op(2))
    assert t.live_text() == "abcZdef"


def test_delete_across_ops_and_restore_only_own_kills():
    t = Text()
    typed(t, "aa", 1)
    typed(t, "bb", 2)
    typed(t, "cc", 3)
    killed = t.delete((op(1), 1), (op(3), 0), by=op(4))
    assert killed == [(op(1), 1, 1), (op(2), 0, 2), (op(3), 0, 1)]
    assert t.live_text() == "ac"
    # A later delete of a surviving char is not undone by restoring op 4's kill.
    t.delete((op(3), 1), (op(3), 1), by=op(5))
    t.revive(killed, by=op(4))
    assert t.live_text() == "aabbc"


def test_bold_grows_after_its_end_but_link_does_not():
    t = Text()
    typed(t, "abc", 1)
    t.set_mark(BOLD, {}, (op(1), 0), (op(1), 2), True)
    t.set_mark(LINK, {"href": "x"}, (op(1), 0), (op(1), 2), True)
    typed(t, "d", 2)
    bold = next(m for m in t.marks if m.kind == BOLD)
    link = next(m for m in t.marks if m.kind == LINK)
    assert bold.end == (op(2), 0)
    assert link.end == (op(1), 2)


def test_bold_merges_and_partial_off_splits():
    t = Text()
    typed(t, "abcdef", 1)
    t.set_mark(BOLD, {}, (op(1), 0), (op(1), 1), True)
    removed, added = t.set_mark(BOLD, {}, (op(1), 2), (op(1), 5), True)
    assert len(t.marks) == 1 and t.marks[0].start == (op(1), 0) and t.marks[0].end == (op(1), 5)
    assert len(removed) == 1
    removed, added = t.set_mark(BOLD, {}, (op(1), 2), (op(1), 3), False)
    assert sorted((m.start[1], m.end[1]) for m in t.marks) == [(0, 1), (4, 5)]
    assert len(removed) == 1 and len(added) == 2


def test_exclusive_link_displaces_cite_and_inverse_restores():
    t = Text()
    typed(t, "abcdef", 1)
    t.set_mark(CITE, {"file": "f", "symbol": "s"}, (op(1), 0), (op(1), 5), True)
    removed, added = t.set_mark(LINK, {"href": "h"}, (op(1), 2), (op(1), 3), True)
    kinds = sorted((m.kind, m.start[1], m.end[1]) for m in t.marks)
    assert kinds == [(CITE, 0, 1), (CITE, 4, 5), (LINK, 2, 3)]
    assert [m.kind for m in removed] == [CITE]
    # Apply the inverse: remove added, restore removed.
    for m in added:
        t.set_mark(m.kind, m.attrs, m.start, m.end, False)
    for m in removed:
        t.set_mark(m.kind, m.attrs, m.start, m.end, True)
    assert [(m.kind, m.start[1], m.end[1]) for m in t.marks] == [(CITE, 0, 5)]


def test_comment_is_whole_only():
    t = Text()
    typed(t, "abcd", 1)
    t.set_mark(COMMENT, {"block": "c1"}, (op(1), 0), (op(1), 3), True)
    with pytest.raises(MarkError):
        t.set_mark(COMMENT, {"block": "c1"}, (op(1), 1), (op(1), 2), False)
    removed, _ = t.set_mark(COMMENT, {"block": "c1"}, (op(1), 0), (op(1), 3), False)
    assert len(removed) == 1 and not t.marks


def test_split_keeps_identity_and_divides_marks():
    t = Text()
    typed(t, "abcdef", 1)
    t.set_mark(BOLD, {}, (op(1), 1), (op(1), 4), True)
    tail = t.split((op(1), 2))
    assert t.live_text() == "abc" and tail.live_text() == "def"
    assert t.marks[0].start == (op(1), 1) and t.marks[0].end == (op(1), 2)
    assert tail.marks[0].start == (op(1), 3) and tail.marks[0].end == (op(1), 4)
    at = t.merge(tail)
    assert at == (op(1), 2)
    assert t.live_text() == "abcdef"
    assert len(t.marks) == 1 and t.marks[0].end == (op(1), 4)


def test_split_at_start_and_merge_of_empty_head():
    t = Text()
    typed(t, "abc", 1)
    tail = t.split(START)
    assert t.live_text() == "" and tail.live_text() == "abc"
    assert t.merge(tail) == "END"
    assert t.live_text() == "abc"


def test_unknown_anchor_is_reported_not_guessed():
    t = Text()
    typed(t, "abc", 1)
    with pytest.raises(AnchorError):
        t.insert((op(9), 0), "x", op=op(2), actor="a", at="2")
    with pytest.raises(AnchorError):
        t.locate((op(1), 3))


def test_random_edits_preserve_identity_invariants():
    rng = random.Random(7)
    t = Text()
    n = 0
    minted: dict[tuple, str] = {}
    for _ in range(400):
        n += 1
        live = list(t.live_refs())
        choice = rng.random()
        if not live or choice < 0.5:
            s = "".join(rng.choice("abc ") for _ in range(rng.randint(1, 5)))
            after = START if not live or rng.random() < 0.1 else rng.choice(live)
            seg = t.insert(after, s, op=op(n), actor=rng.choice(["human:a", "loop"]), at=str(n))
            for i, ch in enumerate(s):
                minted[seg.ref(i)] = ch
        elif choice < 0.8:
            a, b = sorted(rng.sample(range(len(live)), 2)) if len(live) > 1 else (0, 0)
            t.delete(live[a], live[b], by=op(n))
        else:
            a, b = sorted(rng.sample(range(len(live)), 2)) if len(live) > 1 else (0, 0)
            t.set_mark(BOLD, {}, live[a], live[b], rng.random() < 0.7)
        # I1: every character maps back to exactly the text it was minted with.
        for s in t.segs:
            for i, ch in enumerate(s.chars):
                assert minted[s.ref(i)] == ch
        # Segments are in a consistent order: every ref locatable, no duplicates.
        seen = set()
        for s in t.segs:
            for i in range(len(s.chars)):
                r = s.ref(i)
                assert r not in seen
                seen.add(r)
        # Round trip.
        assert Text.from_json(t.to_json()).live_text() == t.live_text()
